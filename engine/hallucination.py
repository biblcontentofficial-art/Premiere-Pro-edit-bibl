"""전사 환각(hallucination) 검출.

whisper 는 무음·노이즈·클리핑 구간에서 같은 토큰을 수십~수백 번 반복하는
repetition loop 에 빠진다. 그 유령 발화가 컷 판정의 근거로 쓰이면 러프컷 전체가
어긋나는데, 기존 게이트는 이걸 하나도 잡지 못한다.

DJI 4K 촬영본 8개 실측 (한국어 토킹헤드, 29.97fps):
  0001  "전화시장에" 44회 (전사의 72%)   ← 리포트「더듬/중복」은 2개로만 셈
  0004  ",1" 110회 (38%) + 끝 2.1초에 "-0" 108토큰
  0006  무음(-54 LUFS)을 40dB 증폭 → "comprisнач리ацию" "Sandra這樣子"
모두「자연스러움 주의 0곳」·「갭/겹침 0」·XML 검증 통과. 전부 초록불이었다.

사용:
  python3 hallucination.py "<base>_words.json"
"""

import json
import re
import sys
from collections import Counter

# 같은 토큰이 이만큼 연속되면 루프로 본다. 정상 발화의 연속 반복은 2~3회가 한계
# ("감사합니다 감사합니다" 등). 5회는 실측 최소 환각(44회)과 충분히 떨어져 있다.
MIN_RUN = 5

# 한 토큰이 전사의 이 비율을 넘으면 편중으로 본다. 연속이 아니라 흩어져 반복되는
# 환각을 잡는다. 정상 대화에서 한 단어가 25%를 넘는 일은 없다.
MAX_SHARE = 0.25

# 이보다 짧은 전사는 비율 판정을 하지 않는다 (표본이 작아 오탐이 난다).
MIN_WORDS = 10

# 루프 끝에서 이 시간(초) 안에 붙어 나오는 부분 토큰은 꼬리로 보고 함께 버린다.
TAIL_GAP = 1.0

# 환각을 걷어낸 뒤 남은 한글 단어가 이보다 적으면 "잘라서 재처리"를 권하지 않는다.
# 건질 게 없는 테이크에 재처리를 안내하면 헛수고를 시킨다 — 0006 실측(2단어).
MIN_REAL = 5

# 한국어 영상에 나오면 이상한 문자대. 라틴 문자는 제외한다 — 브랜드명(MOSCOT,
# ZEISS)이나 흔한 약어(OK, BGM)가 정상적으로 섞이기 때문에 오탐이 심하다.
FOREIGN = re.compile(r"[Ѐ-ӿ一-鿿぀-ヿ�]")

HANGUL = re.compile(r"[가-힣]")


def _runs(tokens):
    """연속 반복 구간을 [(시작 인덱스, 길이, 토큰)] 로."""
    out = []
    i = 0
    while i < len(tokens):
        j = i
        while j + 1 < len(tokens) and tokens[j + 1] == tokens[i]:
            j += 1
        if j - i + 1 >= MIN_RUN:
            out.append((i, j - i + 1, tokens[i]))
        i = j + 1
    return out


def detect(words, min_run=MIN_RUN, max_share=MAX_SHARE):
    """words = [[start, end, text], ...] (words.json 포맷).

    반환 dict:
      suspect     환각 의심 여부 (bool)
      runs        [(시작초, 끝초, 토큰, 반복수)] 연속 루프
      dominant    (토큰, 횟수, 비율) 또는 None — 흩어진 편중
      foreign     [(시작초, 토큰)] 키릴·한자·가나·깨진문자
      valid_span  (시작초, 끝초) 또는 None — 환각을 뺀 유효 발화 구간
      junk        환각으로 판정된 토큰 집합
    """
    res = {"suspect": False, "runs": [], "dominant": None,
           "foreign": [], "valid_span": None, "junk": set()}
    if not words:
        return res

    toks = [w[2] for w in words]

    for i, n, tok in _runs(toks):
        res["runs"].append((words[i][0], words[i + n - 1][1], tok, n))
        res["junk"].add(tok)

    if len(toks) > MIN_WORDS:
        tok, cnt = Counter(toks).most_common(1)[0]
        share = cnt / len(toks)
        if share > max_share:
            res["dominant"] = (tok, cnt, share)
            res["junk"].add(tok)

    for w in words:
        if FOREIGN.search(w[2]):
            res["foreign"].append((w[0], w[2]))
            res["junk"].add(w[2])

    res["suspect"] = bool(res["runs"] or res["dominant"] or res["foreign"])

    # 유효 발화 구간 — 환각을 뺀 한글 단어의 처음~끝.
    # 제외는 토큰 문자열이 아니라 "시간 범위"로 한다. 루프의 잘린 꼬리("전화시장에"
    # 반복 끝의 "전화")는 문자열이 달라 junk 를 빠져나가면서 구간 끝을 20초나
    # 늘려 놓는다 — 0001 실측.
    def _junk(w):
        if w[2] in res["junk"]:
            return True
        for rs, re_, tok, _n in res["runs"]:
            if rs <= w[0] < re_:
                return True
            # 루프의 잘린 꼬리 — 반복이 끊기는 지점에서 whisper 가 토큰을 중간까지만
            # 뱉는다("전화시장에" 44회 뒤의 "전화"). 문자열이 달라 위 검사를 통과하고,
            # 홀로 남아 유효 구간 끝을 20초나 늘려 놓는다 — 0001 실측.
            # 침묵으로 끊는 방식은 쓰지 않는다: 발화 중 3초 침묵은 정상이라
            # 멀쩡한 구간까지 잘라낸다(0004 유효 시작이 28.3→59.3초로 밀렸다).
            if (abs(w[0] - re_) < TAIL_GAP and w[2] != tok
                    and (tok.startswith(w[2]) or w[2] in tok)):
                return True
        return False

    real = [w for w in words if not _junk(w) and HANGUL.search(w[2])]
    res["real_words"] = len(real)
    if real:
        res["valid_span"] = (real[0][0], real[-1][1])
    return res


def summary_lines(res, total_dur=None):
    """리포트/화면에 쓸 사람이 읽는 줄들. 의심 없으면 빈 리스트."""
    if not res["suspect"]:
        return []
    out = []
    for s, e, tok, n in res["runs"]:
        out.append(f"{_t(s)}~{_t(e)}  \"{tok}\" {n}회 연속 반복")
    if res["dominant"]:
        tok, cnt, share = res["dominant"]
        out.append(f"전사 편중  \"{tok}\" {cnt}회 ({share * 100:.0f}%)")
    if res["foreign"]:
        show = ", ".join(f'"{t}"' for _s, t in res["foreign"][:5])
        more = f" 외 {len(res['foreign']) - 5}개" if len(res["foreign"]) > 5 else ""
        out.append(f"이상 문자  {show}{more}  (한국어 영상에 키릴·한자·깨진문자)")
    n = res.get("real_words", 0)
    if res["valid_span"] and n >= MIN_REAL:
        a, b = res["valid_span"]
        tail = ""
        if total_dur and (a > 1 or total_dur - b > 1):
            tail = f"  → 이 구간만 잘라 다시 돌리면 정상화됩니다"
        out.append(f"유효 발화  {_t(a)} ~ {_t(b)}  ({n}단어){tail}")
    else:
        out.append(f"유효 발화  {n}단어뿐 — 건질 게 없습니다 (테이크 폐기 검토)")
    return out


def _t(sec):
    m, s = divmod(float(sec), 60)
    return f"{int(m):02d}:{s:05.2f}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('사용: python3 hallucination.py "<base>_words.json"')
        sys.exit(1)
    ws = json.load(open(sys.argv[1], encoding="utf-8"))
    r = detect(ws)
    print(f"전사 {len(ws)}단어")
    lines = summary_lines(r, ws[-1][1] if ws else None)
    if not lines:
        print("환각 없음")
    else:
        print("[주의] 환각 의심")
        for l in lines:
            print("  " + l)
