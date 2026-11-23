"""走访手记解析。

解析只抽取原话里说得出出处的事实，不做统计推断：

* 识别岸别、地标、相对方向、起止范围、量词；
* 识别"没见到 / 没走到 / 没过秤"等否定；
* 区分搜过的范围、物品原先在的地方、集合或称重地点；
* 标记转述另一个队的数据、合称、同一批。

每条事实都带原句与手记编号，报告可以回查。解析不出来的保留为未决，
绝不按平均值摊到各段，也不把没有观察依据的空白当成零。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .model import Records, Segment

CN_DIGITS = {
    '零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
    '百': 100, '千': 1000, '半': 0.5,
}
_CN_NUM = r'[零〇一二两三四五六七八九十百千]+'
_AN_NUM = r'\d+(?:\.\d+)?'
_NUM = rf'(?:{_AN_NUM}|{_CN_NUM}|半)'

UNIT_ITEM = 'items'
UNIT_BAG = 'bags'
UNIT_WEIGHT = 'weight'
UNIT_LENGTH = 'length'
UNIT_TIME = 'time'
UNIT_SEGMENT = 'segments'

_UNIT_KIND = {
    '个': (UNIT_ITEM, '个'), '只': (UNIT_ITEM, '只'), '件': (UNIT_ITEM, '件'),
    '支': (UNIT_ITEM, '支'), '瓶': (UNIT_ITEM, '瓶'),
    '袋': (UNIT_BAG, '袋'), '批': (UNIT_BAG, '批'),
    '公斤': (UNIT_WEIGHT, '公斤'), '千克': (UNIT_WEIGHT, '千克'), '斤': (UNIT_WEIGHT, '斤'),
    '米': (UNIT_LENGTH, '米'), '分钟': (UNIT_TIME, '分钟'), '段': (UNIT_SEGMENT, '段'),
}

SENTENCE_SPLIT = re.compile(r'(?<=[。；！？])')
CLAUSE_SPLIT = re.compile(r'[，,；;。]')

_SEARCH = re.compile(r'走|扫|查|巡|下去')
_ABSENT = re.compile(r'没见|未见|没见到|没有发现|没发现')
_NOT_WALKED = re.compile(r'没走到|没下去|未下去|没有下去|未走完|没走完|只走到|只走|退了|避水|封着|封了')
_NOT_WEIGHED = re.compile(r'没过秤|没有条件称|无法称|没单称|未称|不能称|没称')
_UNCLEAR = re.compile(r'不能确定|不确定|没走近')
_TRANSCRIBED = re.compile(r'另一队|对岸队|渡台队|渡台交|交去|搬来|自己这边和对岸|补交|对方队|另一岸队')
_SHARED = re.compile(r'合(?:起来)?(?:称|计|算|重)|同一批|一起上秤|一起称|并在同一批|一起重')
_NOT_OURS = re.compile(r'不算[^，。；]{0,8}捡的|另一队搬来|别队搬来|不是本队')
_WEIGHED = re.compile(r'称过|称重|上秤|称了|称得|没过秤|没有条件称|无法称|没单称|未称|不能称|没称|称点')

_DIRECTION_WORDS = {'对岸', '同岸', '另一岸', '上游', '下游', '桥上', '桥边'}
_DIRECTION_HINTS = ('往下', '向上', '上游', '下游', '对岸', '同岸', '另一岸')


def cn_to_number(text: str) -> float | None:
    if not text:
        return None
    if re.fullmatch(_AN_NUM, text):
        return float(text)
    if text == '半':
        return 0.5
    section = number = 0
    for ch in text:
        if ch in '零〇':
            continue
        if ch in '一二三四五六七八九两':
            number = CN_DIGITS[ch]
        elif ch == '十':
            section += (number or 1) * 10
            number = 0
        elif ch == '百':
            section += (number or 1) * 100
            number = 0
        elif ch == '千':
            section += (number or 1) * 1000
            number = 0
        elif ch == '半':
            section += 0.5
        else:
            return None
    return float(section + number)


# --------------------------------------------------------------------------
# 事实
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Landmark:
    name: str
    seg_id: str | None
    bank: str
    position: int | None
    text: str


@dataclass(frozen=True)
class Coverage:
    """一次巡查可核定的覆盖。

    lo/hi 是实际走到的范围；nominal_hi 是原话声称但没走完的终点。
    """

    bank: str
    start_name: str | None
    end_name: str | None
    lo: int
    hi: int
    nominal_hi: int | None
    complete: bool
    minutes: int | None
    rate_per_100m: float | None
    evidence: str
    note_id: str
    visit_id: str
    team: str
    season: str

    @property
    def length(self) -> int:
        return max(0, self.hi - self.lo)

    def seg_ids(self, records: Records) -> tuple[str, ...]:
        return tuple(seg.id for seg, _, _ in records.segments_in(self.bank, self.lo, self.hi))


@dataclass(frozen=True)
class Quantity:
    note_id: str
    visit_id: str
    season: str
    bank: str
    unit_kind: str
    unit: str
    value: float | None
    scope: str                 # segment / group / total / per_segment / per_100m / partial / unresolved / restated
    seg_ids: tuple[str, ...] = ()
    nominal_seg_ids: tuple[str, ...] = ()
    lo: int | None = None
    hi: int | None = None
    nominal_lo: int | None = None
    nominal_hi: int | None = None
    absent: bool = False
    unresolved: bool = False
    uncertain_lower: bool = False
    transcribed: bool = False
    shared: bool = False
    not_ours: bool = False
    shared_banks: tuple[str, ...] = ()
    batch: str | None = None
    same_batch: bool = False
    weigh_point: str | None = None
    item_origin: str | None = None
    evidence: str = ''

    @property
    def is_items(self) -> bool:
        return self.unit_kind == UNIT_ITEM


@dataclass(frozen=True)
class Negation:
    kind: str      # absent / not_walked / not_weighed / unclear
    text: str
    seg_ids: tuple[str, ...]
    bank: str
    note_id: str


@dataclass(frozen=True)
class NoteParse:
    note_id: str
    visit_id: str
    team: str
    visit_bank: str
    season: str
    weather: str
    text: str
    sentences: tuple[str, ...]
    clauses: tuple[str, ...]
    landmarks: tuple[Landmark, ...]
    directions: tuple[tuple[str, str], ...]
    coverage: tuple[Coverage, ...]
    quantities: tuple[Quantity, ...]
    negations: tuple[Negation, ...]
    transcribed_phrases: tuple[str, ...]
    weigh_points: tuple[str, ...]
    collection_points: tuple[str, ...]

    @property
    def absent(self) -> tuple[Negation, ...]:
        return tuple(n for n in self.negations if n.kind == 'absent')

    @property
    def not_walked(self) -> tuple[Negation, ...]:
        return tuple(n for n in self.negations if n.kind == 'not_walked')

    @property
    def not_weighed(self) -> tuple[Negation, ...]:
        return tuple(n for n in self.negations if n.kind == 'not_weighed')


# --------------------------------------------------------------------------
# 地标
# --------------------------------------------------------------------------

def _landmark_table(records: Records) -> list[tuple[str, Segment]]:
    return [(alias, seg) for seg in records.segments for alias in seg.aliases]


def _team_fragments(records: Records) -> set[str]:
    return {v.team[:-1] for v in records.visits if v.team.endswith('队')}


def find_landmarks(text: str, records: Records) -> list[Landmark]:
    table = _landmark_table(records)
    teams = _team_fragments(records)
    hits: list[tuple[int, int, str, Segment]] = []
    taken: list[tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(x < b and a < y for x, y in taken)

    for alias, seg in sorted(table, key=lambda p: len(p[0]), reverse=True):
        start = 0
        while True:
            pos = text.find(alias, start)
            if pos < 0:
                break
            end = pos + len(alias)
            if not overlaps(pos, end):
                taken.append((pos, end))
                hits.append((pos, end, alias, seg))
            start = end

    # 唯一的地标简称，如原话把"柳湾渡口"写作"渡口"。
    for alias, seg in table:
        for size in (2, 3):
            token = alias[-size:]
            if len(token) < 2 or token in _DIRECTION_WORDS or token in teams:
                continue
            if sum(1 for other, _ in table if other.endswith(token)) != 1:
                continue
            for m in re.finditer(re.escape(token), text):
                pos, end = m.start(), m.end()
                if text[end:end + 1] == '队' or overlaps(pos, end):
                    continue
                taken.append((pos, end))
                hits.append((pos, end, token, seg))

    hits.sort(key=lambda h: h[0])
    return [Landmark(name, seg.id, seg.bank, seg.start, text) for _, _, name, seg in hits]


def _sentence_bank(landmarks: list[Landmark], default: str) -> str:
    if landmarks:
        banks = {lm.bank for lm in landmarks}
        if len(banks) == 1:
            return next(iter(banks))
    return default


# --------------------------------------------------------------------------
# 覆盖
# --------------------------------------------------------------------------

def _parse_coverage(
    sentence: str, landmarks: list[Landmark], records: Records,
    note_id: str, visit_id: str, team: str, season: str, bank: str,
) -> list[Coverage]:
    if not landmarks or not _SEARCH.search(sentence):
        return []
    if re.search(r'另一队|搬来|称重|上秤|批号|不算', sentence) and not re.search(r'走|扫|查|巡', sentence):
        return []
    if '没下去' in sentence or '未下去' in sentence or '没有下去' in sentence:
        return []

    first = landmarks[0]
    start = first
    m_from = re.search(r'从\s*([^，。；]{1,12}?)\s*(?:往下|向下|向上|沿|顺)', sentence)
    if m_from:
        frag = m_from.group(1)
        start = next((lm for lm in landmarks if lm.name in frag), first)

    rule = None
    m_rule = re.search(rf'每(?:一)?百米[^，。；]{{0,14}}?({_NUM})\s*分钟', sentence)
    if m_rule:
        rule = float(cn_to_number(m_rule.group(1)))
    elif '按百米折合' in sentence or '每一百米' in sentence:
        m_other = re.search(rf'({_NUM})\s*分钟', sentence)
        rule = float(cn_to_number(m_other.group(1))) if m_other else 20.0

    minutes = None
    m_min = re.search(rf'({_NUM})\s*分钟', sentence)
    if m_min and not m_rule:
        value = cn_to_number(m_min.group(1))
        minutes = int(value) if value is not None else None

    m_explicit = re.search(rf'(?:实际只走到|只走到|走到|查过|到)\s*({_AN_NUM}|[一二两三四五六七八九十百千]+)\s*(?:米|处)', sentence)
    explicit_pos = int(cn_to_number(m_explicit.group(1))) if m_explicit else None

    half_lm = None
    for lm in landmarks:
        if re.search(rf'{re.escape(lm.name)}[^，。；]{{0,8}}一半', sentence) or \
           re.search(rf'到\s*{re.escape(lm.name)}\s*一半', sentence):
            half_lm = lm
            break

    end = None
    for lm in landmarks:
        if lm is start:
            continue
        if re.search(rf'{re.escape(lm.name)}\s*(?:末端|尽头|为止|止)', sentence) or \
           re.search(rf'(?:到|至|了)\s*{re.escape(lm.name)}', sentence):
            end = lm
            break
    if end is None:
        for lm in landmarks:
            if lm is not start and lm.name in sentence and lm is not half_lm:
                end = lm
                break

    m_two = re.search(r'([一二两三四五六七八九十])\s*段', sentence)
    nominal_two = int(cn_to_number(m_two.group(1))) if m_two else None

    start_seg = records.segment(start.seg_id) if start.seg_id else None
    if start_seg is None:
        return []

    lo = start_seg.start
    nominal = None
    hi = None
    complete = not bool(_NOT_WALKED.search(sentence))

    if half_lm and half_lm.seg_id:
        hs = records.segment(half_lm.seg_id)
        lo, hi, nominal = hs.start, hs.start + hs.length // 2, hs.end
    elif end and end.seg_id:
        hi = records.segment(end.seg_id).end
        nominal = hi
    elif nominal_two and nominal_two >= 1:
        ordered = records.segments_of(bank)
        idx = ordered.index(start_seg) if start_seg in ordered else None
        if idx is not None:
            last = ordered[min(idx + nominal_two - 1, len(ordered) - 1)]
            hi = last.end
            nominal = hi
    elif explicit_pos is None:
        hi = start_seg.end

    if explicit_pos is not None:
        hi = explicit_pos
        if nominal is not None and nominal < hi:
            nominal = hi

    if hi is None:
        return []
    if nominal is not None and nominal <= hi:
        nominal = None

    rate = rule
    if rate is None and minutes is not None and hi > lo:
        rate = minutes / ((hi - lo) / 100)

    return [Coverage(
        bank=bank, start_name=start.name,
        end_name=end.name if end else (half_lm.name if half_lm else None),
        lo=lo, hi=hi, nominal_hi=nominal, complete=complete,
        minutes=minutes, rate_per_100m=rate, evidence=sentence.strip(),
        note_id=note_id, visit_id=visit_id, team=team, season=season,
    )]


# --------------------------------------------------------------------------
# 数量
# --------------------------------------------------------------------------

def _first_n_segments(records: Records, bank: str, n: int) -> tuple[str, ...]:
    return tuple(seg.id for seg in records.segments_of(bank)[:n])


def _item_scope(clause: str, n_landmarks: int) -> str:
    if re.search(r'每\s*(?:一)?段', clause):
        return 'per_segment'
    if re.search(r'每(?:一)?百米', clause):
        return 'per_100m'
    if re.search(r'[两三四五六七八九十]\s*段|各段|那两段|前两段', clause):
        return 'group'
    if re.search(r'共|合计|总共|总计|总', clause):
        return 'group' if n_landmarks >= 2 else 'total'
    return 'segment' if n_landmarks == 1 else ('group' if n_landmarks >= 2 else 'unresolved')


def _parse_quantities_clause(
    clause: str, clause_lms: list[Landmark], sentence_lms: list[Landmark],
    records: Records, note_id: str, visit_id: str, season: str, bank: str,
    sentence: str,
) -> list[Quantity]:
    out: list[Quantity] = []
    lms = clause_lms or sentence_lms
    shared = bool(_SHARED.search(clause))
    transcribed = bool(_TRANSCRIBED.search(clause))
    not_ours = bool(_NOT_OURS.search(clause))
    m_batch = re.search(r'(?:批号|批次|写作|批)\s*([一-龥A-Za-z0-9]+)', clause)
    batch = m_batch.group(1) if m_batch else None
    same_batch = '同一批' in clause
    weigh = None
    m_point = re.search(r'称点(?:在|是)\s*([^，。；]{1,10}?)(?:[，。；]|$)', clause)
    if m_point:
        weigh = m_point.group(1)
    if weigh is None:
        m_point = re.search(r'(?:在|于)\s*([^，。；]{1,10}?)\s*(?:称|上秤)', clause)
        if m_point:
            weigh = m_point.group(1)
    if weigh is None:
        m_point = re.search(r'([^，。；]{1,8}?)\s*(?:另外)?称过', clause)
        if m_point:
            weigh = m_point.group(1)
    m_origin = re.search(r'([^，。；]{0,8}?)(?:水里|堤顶|岸边)', clause)
    origin = m_origin.group(0) if m_origin else None
    shared_banks: tuple[str, ...] = ()
    if transcribed and re.search(r'对岸|渡台队|另一队', clause):
        shared_banks = tuple(sorted({bank, '右岸' if bank == '左岸' else '左岸'}))

    weighed_clause = bool(_WEIGHED.search(clause)) or bool(re.search(r'总|全队|合计|共', clause)) and bool(weigh)
    seg_ids = tuple(dict.fromkeys(lm.seg_id for lm in lms if lm.seg_id))
    scope = _item_scope(clause, len(lms))

    # "前两段" 指的是本岸从起点数的前几段，不能挂到同句里后出现的"桥下"
    if re.search(r'前\s*([一二两三四五六七八九十])\s*段', clause):
        n = int(cn_to_number(re.search(r'前\s*([一二两三四五六七八九十])\s*段', clause).group(1)))
        seg_ids = _first_n_segments(records, bank, n)
        scope = 'group'

    pattern = re.compile(
        rf'(?P<num>{_NUM})\s*(?P<unit>个|只|件|支|瓶|袋|批|公斤|千克|斤)(?P<tail>[^，。；]{{0,16}})'
    )
    for m in pattern.finditer(clause):
        raw_num = m.group('num')
        if raw_num == '半':
            continue  # 交给下面的“半袋”规则，避免重复
        kind, unit = _UNIT_KIND[m.group('unit')]
        value = cn_to_number(raw_num)
        q_scope = scope
        q_seg_ids = seg_ids
        # 称重/集合总量：属于集合或称重点，不摊回各段
        if kind in (UNIT_WEIGHT, UNIT_BAG) and weighed_clause:
            q_scope = 'total'
            q_seg_ids = ()
        if kind == UNIT_WEIGHT and (transcribed or shared):
            q_scope = 'total'
            q_seg_ids = ()
        if kind == UNIT_ITEM and re.search(r'另外|另见|另看到', clause):
            q_scope = 'segment' if len(lms) == 1 else q_scope
        out.append(Quantity(
            note_id=note_id, visit_id=visit_id, season=season, bank=bank,
            unit_kind=kind, unit=unit, value=value, scope=q_scope,
            seg_ids=q_seg_ids, nominal_seg_ids=seg_ids if q_scope == 'group' else (),
            transcribed=transcribed, shared=shared, not_ours=not_ours,
            shared_banks=shared_banks, batch=batch, same_batch=same_batch,
            weigh_point=weigh, item_origin=origin,
            uncertain_lower=bool(re.search(r'至少|不止', m.group('tail'))),
            evidence=clause.strip(),
        ))

    for m in re.finditer(r'半\s*(袋|批)', clause):
        out.append(Quantity(
            note_id=note_id, visit_id=visit_id, season=season, bank=bank,
            unit_kind=UNIT_BAG, unit=m.group(1), value=0.5, scope='partial',
            seg_ids=seg_ids, transcribed=transcribed, shared=True,
            not_ours=not_ours, shared_banks=shared_banks, batch=batch,
            same_batch=same_batch, weigh_point=weigh, item_origin=origin,
            evidence=clause.strip(),
        ))
    return out


# --------------------------------------------------------------------------
# 手记
# --------------------------------------------------------------------------

def parse_note(records: Records, note_id: str) -> NoteParse:
    note = records.note(note_id)
    visit = records.visit(note.visit)
    sentences = tuple(s.strip() for s in SENTENCE_SPLIT.split(note.text) if s.strip())
    clauses = tuple(c.strip() for c in CLAUSE_SPLIT.split(note.text) if c.strip())

    landmarks: list[Landmark] = []
    directions: list[tuple[str, str]] = []
    coverage: list[Coverage] = []
    quantities: list[Quantity] = []
    negations: list[Negation] = []
    transcribed: list[str] = []
    weigh_points: list[str] = []
    collection_points: list[str] = []

    for sentence in sentences:
        s_lms = find_landmarks(sentence, records)
        bank = _sentence_bank(s_lms, visit.bank)
        landmarks.extend(s_lms)
        for d in _DIRECTION_HINTS:
            if d in sentence:
                directions.append((d, sentence))

        claims = _parse_coverage(sentence, s_lms, records, note.id, visit.id, visit.team, visit.season, bank)
        coverage.extend(claims)

        seg_ids = tuple(dict.fromkeys(lm.seg_id for lm in s_lms if lm.seg_id))
        s_quantities: list[Quantity] = []
        for clause in (c.strip() for c in CLAUSE_SPLIT.split(sentence) if c.strip()):
            c_lms = find_landmarks(clause, records)
            c_bank = _sentence_bank(c_lms, bank)
            s_quantities.extend(_parse_quantities_clause(
                clause, c_lms, s_lms, records, note.id, visit.id, visit.season,
                c_bank, sentence,
            ))
            clause_seg_ids = tuple(dict.fromkeys(lm.seg_id for lm in (c_lms or s_lms) if lm.seg_id))
            if _NOT_WALKED.search(clause):
                negations.append(Negation('not_walked', clause, clause_seg_ids, c_bank, note.id))
            if _NOT_WEIGHED.search(clause):
                negations.append(Negation('not_weighed', clause, clause_seg_ids, c_bank, note.id))
            if _UNCLEAR.search(clause):
                negations.append(Negation('unclear', clause, clause_seg_ids, c_bank, note.id))
            if re.search(r'袋子在|放在|搬到|在堤顶', clause):
                m = re.search(r'(?:袋子在|放在|搬到|在)([^，。；]{1,10})', clause)
                if m:
                    collection_points.append(m.group(1))

        has_positive_items = any(q.unit_kind == UNIT_ITEM and q.value for q in s_quantities)
        if _ABSENT.search(sentence) and not has_positive_items:
            for claim in claims:
                negations.append(Negation('absent', sentence.strip(),
                                          claim.seg_ids(records), claim.bank, note.id))
        elif _ABSENT.search(sentence) and claims and not has_positive_items:
            negations.append(Negation('absent', sentence.strip(), seg_ids, bank, note.id))

        for q in s_quantities:
            if not q.seg_ids and q.scope == 'segment' and seg_ids:
                q = replace(q, seg_ids=seg_ids)
            if not q.seg_ids and q.scope == 'unresolved' and seg_ids:
                q = replace(q, seg_ids=seg_ids)
            quantities.append(q)

        if _TRANSCRIBED.search(sentence):
            transcribed.append(sentence.strip())
        if re.search(r'(?:在|于|称点在|称过)\s*[^，。；]{1,10}?\s*(?:称|上秤|称重)', sentence):
            m = re.search(r'(?:在|于|称点在|称过)\s*([^，。；]{1,10}?)\s*(?:称|上秤|称重)', sentence)
            if m:
                weigh_points.append(m.group(1))

    quantities = list(_merge_batch_mentions(quantities, note))
    quantities = list(_attach_note_weigh_point(quantities, note))
    quantities = list(_resolve_group_ranges(quantities, coverage, records, note, visit))
    quantities = list(_resolve_unclear_items(quantities, negations, note, visit))

    # 同一条手记另一句给出的每百米时数，补到本手记的覆盖上
    rates = [c.rate_per_100m for c in coverage if c.rate_per_100m is not None]
    if not rates:
        m_note = re.search(rf'每(?:一)?百米[^，。；]{{0,14}}?({_NUM})\s*分钟', note.text)
        if m_note:
            value = cn_to_number(m_note.group(1))
            rates = [float(value)] if value is not None else []
        elif '按百米折合' in note.text:
            rates = [20.0]
    if rates:
        coverage = [replace(c, rate_per_100m=c.rate_per_100m if c.rate_per_100m is not None else rates[0])
                    for c in coverage]

    return NoteParse(
        note_id=note.id, visit_id=visit.id, team=visit.team,
        visit_bank=visit.bank, season=visit.season, weather=visit.weather,
        text=note.text, sentences=sentences, clauses=clauses,
        landmarks=tuple(dict.fromkeys(landmarks)),
        directions=tuple(dict.fromkeys(directions)),
        coverage=tuple(dict.fromkeys(coverage)),
        quantities=tuple(quantities),
        negations=tuple(dict.fromkeys(negations)),
        transcribed_phrases=tuple(dict.fromkeys(transcribed)),
        weigh_points=tuple(dict.fromkeys(weigh_points)),
        collection_points=tuple(dict.fromkeys(collection_points)),
    )


def _merge_batch_mentions(quantities: list[Quantity], note) -> list[Quantity]:
    """同一条手记另一句补的批号，挂回本条的重量/袋数。"""
    batches = [q.batch for q in quantities if q.batch]
    if not batches:
        return quantities
    batch = batches[0]
    out = []
    for q in quantities:
        if q.batch is None and q.unit_kind in (UNIT_BAG, UNIT_WEIGHT) and q.value:
            q = replace(q, batch=batch)
        out.append(q)
    return out


def _attach_note_weigh_point(quantities: list[Quantity], note) -> list[Quantity]:
    """称重点可能写在另一句（如"称点在桥下；批号柳17"），补到本手记的袋数/重量。"""
    m = re.search(r'称点(?:在|是)\s*([^，。；]{1,10}?)(?:[，。；]|$)', note.text)
    if not m:
        m = re.search(r'(?:在|于)\s*([^，。；]{1,10}?)\s*(?:称重|上秤|称了)', note.text)
    if not m:
        m = re.search(r'([^，。；]{1,8}?)\s*(?:另外)?称过', note.text)
    if not m:
        return quantities
    point = m.group(1)
    return [
        replace(q, weigh_point=q.weigh_point or point)
        if q.unit_kind in (UNIT_BAG, UNIT_WEIGHT) else q
        for q in quantities
    ]


def _resolve_group_ranges(
    quantities: list[Quantity], coverage: tuple[Coverage, ...],
    records: Records, note, visit,
) -> list[Quantity]:
    """把"那两段/前两段"的组总量挂到实际观察范围。

    优先使用本手记的覆盖；同一队员另有手记时，并入同次巡查的覆盖。
    有效范围与实际声称范围都保留，比较时不擅自按平均分摊。
    """
    eff = [c for c in coverage if c.length > 0]
    if not eff:
        return quantities
    lo = min(c.lo for c in eff)
    hi = max(c.hi for c in eff)
    nominal_hi = max((c.nominal_hi or c.hi) for c in eff)
    bank = eff[0].bank
    seg_ids = tuple(seg.id for seg, _, _ in records.segments_in(bank, lo, hi))
    all_ids = tuple(seg.id for seg, _, _ in records.segments_in(bank, lo, nominal_hi))
    out = []
    for q in quantities:
        if q.unit_kind == UNIT_ITEM and not q.seg_ids and q.scope == 'group':
            q = replace(
                q, seg_ids=seg_ids, nominal_seg_ids=all_ids,
                lo=lo, hi=hi, nominal_lo=lo, nominal_hi=nominal_hi,
            )
        out.append(q)
    return out


def _resolve_unclear_items(
    quantities: list[Quantity], negations: tuple[Negation, ...], note, visit,
) -> list[Quantity]:
    """'另看到瓶，没走近，不能确定在哪段'：保留为未决观察，不是零。"""
    if not any(n.kind == 'unclear' for n in negations):
        return quantities
    if any(q.unit_kind == UNIT_ITEM for q in quantities):
        return quantities
    if '瓶' not in note.text and '件' not in note.text and '个' not in note.text:
        return quantities
    text = '；'.join(n.text for n in negations if n.kind == 'unclear')
    origin = None
    m_origin = re.search(r'([^，。；]{0,8}?)(?:水里|堤顶|岸边|桥边)', note.text)
    if m_origin:
        origin = m_origin.group(0)
    return quantities + [Quantity(
        note_id=note.id, visit_id=visit.id, season=visit.season, bank=visit.bank,
        unit_kind=UNIT_ITEM, unit='瓶', value=None, scope='unresolved',
        unresolved=True, item_origin=origin, evidence=text,
    )]


def parse_all(records: Records) -> dict[str, NoteParse]:
    return {note.id: parse_note(records, note.id) for note in records.notes}
