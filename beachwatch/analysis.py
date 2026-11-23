"""观察、覆盖、可比范围与未决差异。

原则：
* 没有走完的范围，只把实际能核定的部分算作有效覆盖；
* 组总量（"两段共 10 个"）不能按平均摊到各段，也不按段重复累计；
* 两人记的同一袋只算一袋，同一批只算一次；
* 没有观察也没有计量依据的空白是"无资料"，不是零；
* 观察保留，但环境/作业时长不达公开口径时，不进入比较；
* 几种合理归属会改变结论时，列为未决差异，不替分析员选。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from .model import Records, Round, Segment
from .parser import UNIT_BAG, UNIT_ITEM, UNIT_WEIGHT, NoteParse, Quantity

METRIC_ITEMS = 'items'
METRIC_WEIGHT = 'weight'


# --------------------------------------------------------------------------
# 公开口径
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Criteria:
    round_id: str
    metric: str
    seasons: tuple[str, ...]
    allowed_weather: tuple[str, ...]
    min_overlap: int
    base_rate: float | None
    tolerance: float
    same_duration: bool

    def rate_ok(self, rate: float | None) -> tuple[bool, str]:
        if rate is None:
            return False, '没有可核对的作业时长记录'
        if self.base_rate is None:
            return True, f'口径未给出可核算的每百米时数；记录 {rate:g}/百米'
        lo, hi = self.base_rate - self.tolerance, self.base_rate + self.tolerance
        if lo <= rate <= hi:
            return True, f'{rate:g} 分钟/百米，在 {lo:g}–{hi:g} 内'
        return False, f'{rate:g} 分钟/百米，超出 {lo:g}–{hi:g}'

    def weather_ok(self, weather: str) -> tuple[bool, str]:
        if weather in self.allowed_weather:
            return True, f'天气“{weather}”在允许范围内'
        return False, f'天气“{weather}”不在允许范围 {"、".join(self.allowed_weather)} 内'


def criteria_for(round_row: Round) -> Criteria:
    metric = METRIC_ITEMS if ('件' in round_row.metric or '瓶' in round_row.metric) else METRIC_WEIGHT
    m_rate = re.search(r'每(?:一)?百米[^0-9]{0,12}(\d+)\s*分钟', round_row.effort)
    base = float(m_rate.group(1)) if m_rate else None
    m_tol = re.search(r'浮差[^0-9]{0,6}(\d+)\s*分钟', round_row.effort)
    tol = float(m_tol.group(1)) if m_tol else 0.0
    return Criteria(
        round_id=round_row.id, metric=metric, seasons=round_row.seasons,
        allowed_weather=round_row.allowed_weather, min_overlap=round_row.min_overlap,
        base_rate=base, tolerance=tol,
        same_duration=('相同作业时长' in round_row.effort),
    )


def criteria_by_metric(records: Records) -> dict[str, Criteria]:
    return {criteria_for(r).metric: criteria_for(r) for r in records.rounds}


# --------------------------------------------------------------------------
# 观察
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Observation:
    obs_id: str
    note_id: str
    visit_id: str
    season: str
    team: str
    bank: str                     # 左岸 / 右岸 / 两岸
    unit_kind: str
    unit: str
    value: float | None
    scope: str
    status: str                   # exact / zero / group / unresolved / bank_total / joint
    lo: int | None
    hi: int | None
    nominal_hi: int | None
    seg_ids: tuple[str, ...]
    complete: bool
    minutes: int | None
    rate: float | None
    weather: str
    transcribed: bool = False
    shared: bool = False
    shared_banks: tuple[str, ...] = ()
    batch: str | None = None
    same_batch: bool = False
    weigh_point: str | None = None
    item_origin: str | None = None
    uncertain_lower: bool = False
    unresolved: bool = False
    mentions: tuple[str, ...] = ()
    evidence: str = ''

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass(frozen=True)
class BagUnit:
    unit_id: str
    value: float | None
    batch: str | None
    owner_team: str
    bank: str
    season: str
    weigh_point: str | None
    mentions: tuple[str, ...]
    folded_into: str | None
    note: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass(frozen=True)
class Unresolved:
    note_id: str
    season: str
    bank: str
    kind: str
    possible_seg_ids: tuple[str, ...]
    text: str
    affects: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _visit_rate(parses: dict[str, NoteParse], visit_id: str) -> float | None:
    for parse in parses.values():
        if parse.visit_id != visit_id:
            continue
        for claim in parse.coverage:
            if claim.rate_per_100m is not None:
                return claim.rate_per_100m
    return None


def _visit_span(
    parses: dict[str, NoteParse], visit_id: str, bank: str,
) -> tuple[int, int, int | None, tuple[str, ...]] | None:
    spans = [c for p in parses.values() if p.visit_id == visit_id
             for c in p.coverage if c.bank == bank]
    if not spans:
        return None
    lo = min(c.lo for c in spans)
    hi = max(c.hi for c in spans)
    nominal = max((c.nominal_hi for c in spans if c.nominal_hi), default=None)
    return lo, hi, nominal, tuple(dict.fromkeys(c.note_id for c in spans))


def _visit_context(
    quantity: Quantity, parses: dict[str, NoteParse],
) -> tuple[int | None, int | None, int | None, float | None, tuple[str, ...]]:
    """给数量补上同一次巡查的作业范围与时长；只补上下文，不改归属。"""
    if quantity.lo is not None and quantity.hi is not None:
        nominal = quantity.nominal_hi if quantity.nominal_hi is not None else None
        return quantity.lo, quantity.hi, nominal, _visit_rate(parses, quantity.visit_id), ()
    span = _visit_span(parses, quantity.visit_id, quantity.bank)
    if span is None:
        return None, None, None, _visit_rate(parses, quantity.visit_id), ()
    lo, hi, nominal, notes = span
    return lo, hi, nominal, _visit_rate(parses, quantity.visit_id), notes


def status_is_group(quantity: Quantity, seg_ids: tuple[str, ...]) -> bool:
    if quantity.scope in ('group', 'per_segment', 'per_100m'):
        return True
    return len(seg_ids) > 1


def _item_observation(
    quantity: Quantity, parse: NoteParse, parses: dict[str, NoteParse], records: Records,
) -> Observation | None:
    if quantity.unresolved or quantity.value is None:
        return None
    lo, hi, nominal_hi, rate, ctx = _visit_context(quantity, parses)
    seg_ids = tuple(quantity.seg_ids)
    if not seg_ids and quantity.nominal_seg_ids:
        seg_ids = tuple(quantity.nominal_seg_ids)
    if not seg_ids and lo is not None and hi is not None:
        seg_ids = tuple(seg.id for seg, _, _ in records.segments_in(quantity.bank, lo, hi))
    if not seg_ids:
        return None

    # 组总量只覆盖被点名的段；与本次实际走到的范围取交集，
    # 不把没走完的部分当成已观察。
    if status_is_group(quantity, seg_ids):
        named = [records.segment(s) for s in seg_ids if s]
        if named:
            g_lo = min(s.start for s in named)
            g_hi = max(s.end for s in named)
            if lo is not None and hi is not None:
                lo, hi = max(lo, g_lo), min(hi, g_hi)
            else:
                lo, hi = g_lo, g_hi
            if nominal_hi is not None:
                nominal_hi = min(nominal_hi, g_hi)
            if hi <= lo:
                return None

    status = 'exact' if (len(seg_ids) == 1 and quantity.scope in ('segment', 'total')) else 'group'
    complete = bool(quantity.evidence) and not parse.not_walked
    return Observation(
        obs_id=f'{quantity.note_id}:items:{quantity.value:g}',
        note_id=quantity.note_id, visit_id=quantity.visit_id, season=quantity.season,
        team=parse.team, bank=quantity.bank, unit_kind=UNIT_ITEM, unit=quantity.unit,
        value=quantity.value, scope=quantity.scope, status=status,
        lo=lo, hi=hi, nominal_hi=nominal_hi, seg_ids=seg_ids, complete=complete,
        minutes=None, rate=rate, weather=parse.weather,
        transcribed=quantity.transcribed, shared=quantity.shared,
        shared_banks=quantity.shared_banks, batch=quantity.batch,
        same_batch=quantity.same_batch, uncertain_lower=quantity.uncertain_lower,
        evidence=quantity.evidence, mentions=tuple(dict.fromkeys((quantity.note_id,) + ctx)),
    )


def _zero_observations(parse: NoteParse, records: Records) -> list[Observation]:
    out = []
    for neg in parse.negations:
        if neg.kind != 'absent':
            continue
        for claim in parse.coverage:
            if not claim.complete:
                continue
            for seg_id in sorted(set(claim.seg_ids(records)) & set(neg.seg_ids)):
                out.append(Observation(
                    obs_id=f'{parse.note_id}:zero:{seg_id}',
                    note_id=parse.note_id, visit_id=parse.visit_id, season=parse.season,
                    team=parse.team, bank=claim.bank, unit_kind=UNIT_ITEM, unit='瓶',
                    value=0.0, scope='segment', status='zero',
                    lo=records.segment(seg_id).start, hi=records.segment(seg_id).end,
                    nominal_hi=None, seg_ids=(seg_id,), complete=True,
                    minutes=claim.minutes, rate=claim.rate_per_100m,
                    weather=parse.weather, evidence=neg.text,
                ))
    return out


def _weight_observation(
    quantity: Quantity, parse: NoteParse, parses: dict[str, NoteParse], records: Records,
) -> Observation:
    shared = quantity.shared or bool(quantity.shared_banks)
    lo, hi, nominal_hi, rate, ctx = _visit_context(quantity, parses)
    return Observation(
        obs_id=f'{quantity.note_id}:weight:{quantity.value:g}',
        note_id=quantity.note_id, visit_id=quantity.visit_id, season=quantity.season,
        team=parse.team, bank='两岸' if shared else quantity.bank,
        unit_kind=UNIT_WEIGHT, unit=quantity.unit, value=quantity.value,
        scope=quantity.scope, status='joint' if shared else 'bank_total',
        lo=lo, hi=hi, nominal_hi=nominal_hi, seg_ids=(), complete=False,
        minutes=None, rate=rate, weather=parse.weather,
        transcribed=quantity.transcribed, shared=shared,
        shared_banks=quantity.shared_banks or (('左岸', '右岸') if shared else ()),
        batch=quantity.batch, same_batch=quantity.same_batch,
        weigh_point=quantity.weigh_point, item_origin=quantity.item_origin,
        evidence=quantity.evidence,
        mentions=tuple(dict.fromkeys((quantity.note_id,) + ctx)),
    )


def _locate_unresolved(
    quantity: Quantity, parse: NoteParse, records: Records,
) -> tuple[str, ...]:
    """用原话的地标约束未定归属的观察，而不是把整条岸都算成可能。

    N8 说"在桥边水里另看到瓶，没走近，不能确定在哪段"：它约束在桥边，
    因此不可能是上游远处的"对岸渡台"段。约束越紧，已有独立记录的段
    越不会被无谓地判成未决。
    """
    bank_segments = records.segments_of(parse.visit_bank)
    if not bank_segments:
        return ()
    origin = quantity.item_origin or ''
    matched: list[Segment] = []
    for seg in bank_segments:
        for alias in seg.aliases:
            core = alias.translate(str.maketrans('', '', '上下对岸湾'))
            if core and core in origin:
                matched.append(seg)
                break
    if not matched:
        walked = {cid for claim in parse.coverage
                  for cid in claim.seg_ids(records)}
        candidates = [s for s in bank_segments if not walked or s.id in walked]
        if not candidates:
            candidates = list(bank_segments)
        return tuple(s.id for s in candidates)

    # 地标在段界上时，桥两侧都可能：向下游扩，也向上游扩一段。
    # 原话说"桥边/水边"本身就是指边界，不能只挑其中一侧。
    boundary = bool(re.search(r'桥边|水边|岸边|界|交界', origin))
    out: list[Segment] = []
    for seg in matched:
        idx = bank_segments.index(seg)
        if boundary and idx - 1 >= 0:
            out.append(bank_segments[idx - 1])
        out.append(seg)
        if idx + 1 < len(bank_segments):
            out.append(bank_segments[idx + 1])
        if idx + 2 < len(bank_segments) and re.search(r'水里|桥下|桥边', origin):
            out.append(bank_segments[idx + 2])
    return tuple(s.id for s in dict.fromkeys(out))


def build_observations(
    records: Records, parses: dict[str, NoteParse],
) -> tuple[list[Observation], list[BagUnit], list[Unresolved]]:
    observations: list[Observation] = []
    bag_quantities: list[tuple[Quantity, NoteParse]] = []
    unresolved: list[Unresolved] = []

    for nid, parse in parses.items():
        for quantity in parse.quantities:
            if quantity.unit_kind == UNIT_ITEM:
                if quantity.unresolved:
                    possible = _locate_unresolved(quantity, parse, records)
                    unresolved.append(Unresolved(
                        note_id=nid, season=parse.season, bank=parse.visit_bank,
                        kind='items_unlocated', possible_seg_ids=possible,
                        text=quantity.evidence,
                        affects='这些瓶可能落在 ' + '、'.join(possible) +
                                ' 之一（原话没走近、不能确定在哪段）；'
                                '会改变这些段及所在岸的件数结论，也可能改变改善与否。'
                                '若它不可能落入某段，那段已有的独立记录仍然成立。',
                    ))
                    continue
                obs = _item_observation(quantity, parse, parses, records)
                if obs:
                    observations.append(obs)
            elif quantity.unit_kind == UNIT_WEIGHT and quantity.value is not None:
                observations.append(_weight_observation(quantity, parse, parses, records))
            elif quantity.unit_kind == UNIT_BAG:
                bag_quantities.append((quantity, parse))
        observations.extend(_zero_observations(parse, records))

    observations = _merge_weight_restatements(observations)
    observations = _attach_batch(observations, parses)
    bags = build_bags(bag_quantities, parses, observations)
    return observations, bags, unresolved


def _merge_weight_restatements(observations: list[Observation]) -> list[Observation]:
    out: list[Observation] = []
    index: dict[tuple, int] = {}
    for obs in observations:
        if obs.unit_kind != UNIT_WEIGHT or obs.value is None:
            out.append(obs)
            continue
        key = (obs.visit_id, obs.value, obs.batch)
        if key in index:
            prev = out[index[key]]
            out[index[key]] = replace(
                prev, mentions=tuple(dict.fromkeys(prev.mentions + obs.mentions + (obs.note_id,))),
                evidence=prev.evidence + ' / ' + obs.evidence,
                weigh_point=prev.weigh_point or obs.weigh_point,
                transcribed=prev.transcribed or obs.transcribed,
                shared=prev.shared or obs.shared,
                same_batch=prev.same_batch or obs.same_batch,
            )
        else:
            index[key] = len(out)
            out.append(replace(obs, mentions=tuple(dict.fromkeys(obs.mentions + (obs.note_id,)))))
    return out


def _attach_batch(observations: list[Observation], parses: dict[str, NoteParse]) -> list[Observation]:
    """手记另一句补的批号，挂回本场的重量与袋数；不改变重量本身。"""
    season_batch: dict[str, str] = {}
    season_same: dict[str, bool] = {}
    for parse in parses.values():
        for q in parse.quantities:
            if q.batch:
                season_batch.setdefault(parse.season, q.batch)
            if q.same_batch:
                season_same[parse.season] = True
    out = []
    for obs in observations:
        if obs.unit_kind == UNIT_WEIGHT and obs.batch is None and obs.season in season_batch:
            obs = replace(obs, batch=season_batch[obs.season],
                          same_batch=season_same.get(obs.season, obs.same_batch))
        out.append(obs)
    return out


def build_bags(
    bag_quantities: list[tuple[Quantity, NoteParse]], parses: dict[str, NoteParse],
    observations: list[Observation],
) -> list[BagUnit]:
    """同一次巡查、同一批次里被反复提到的同一袋只算一袋。

    同一袋可能被两队、两条手记分别记录（如 N2/N3 与 N8 的柳17半袋）：
    批号相同、季节相同、数量相同时合并为同一单位。
    """
    if not bag_quantities:
        return []
    season_batch: dict[str, str] = {}
    for parse in parses.values():
        for q in parse.quantities:
            if q.batch:
                season_batch.setdefault(parse.season, q.batch)

    groups: dict[tuple, list[tuple[Quantity, NoteParse]]] = {}
    key_meta: dict[tuple, tuple[str, str | None]] = {}
    for quantity, parse in bag_quantities:
        batch = quantity.batch or season_batch.get(quantity.season)
        if batch:
            key = ('batch', quantity.season, quantity.value, batch)
            key_meta[key] = (quantity.season, batch)
        else:
            key = ('visit', quantity.visit_id, quantity.value, quantity.bank)
            key_meta[key] = (quantity.season, None)
        groups.setdefault(key, []).append((quantity, parse))

    weights_by_season = {o.season: o for o in observations
                         if o.unit_kind == UNIT_WEIGHT and o.value is not None}
    units: list[BagUnit] = []
    for key, items in groups.items():
        season, batch = key_meta[key]
        value = key[2]
        quantity, parse = items[0]
        mentions = tuple(dict.fromkeys(p.note_id for _, p in items))
        other = any(q.transcribed or q.not_ours for q, _ in items)
        owner = parse.team
        if other:
            owner = '渡台队' if any('渡台' in q.evidence for q, _ in items) else '另一队'
        linked = None
        linked_obs = None
        for obs in observations:
            if obs.unit_kind != UNIT_WEIGHT or obs.season != season:
                continue
            if batch and obs.batch == batch:
                linked, linked_obs = obs.obs_id, obs
                break
            if obs.shared and set(obs.mentions) & set(mentions):
                linked, linked_obs = obs.obs_id, obs
                break
            if obs.note_id in mentions and not obs.shared:
                linked, linked_obs = obs.obs_id, obs
                break
        weigh_point = quantity.weigh_point or (linked_obs.weigh_point if linked_obs else None)
        units.append(BagUnit(
            unit_id=f'bag:{season}:{value:g}:{batch or "无批号"}',
            value=value, batch=batch, owner_team=owner,
            bank=quantity.bank, season=season, weigh_point=weigh_point,
            mentions=mentions, folded_into=linked,
            note=('已并入同一批的称重，不能再单独计一次重量。' if linked
                  else '只作为袋数记录，没有单独称重。'),
        ))
    return units


# --------------------------------------------------------------------------
# 段级状态
# --------------------------------------------------------------------------

@dataclass
class SegmentState:
    seg_id: str
    bank: str
    season: str
    length: int
    covered: list[tuple[int, int, bool, str]] = field(default_factory=list)
    item_status: str = 'no_data'
    item_value: float | None = None
    item_upper: float | None = None
    item_note: str = ''
    item_obs: tuple[str, ...] = ()
    weight_status: str = 'no_data'
    weight_value: float | None = None
    weight_note: str = ''
    citations: tuple[str, ...] = ()

    @property
    def covered_intervals(self) -> list[tuple[int, int]]:
        return [(a, b) for a, b, _, _ in self.covered]

    @property
    def union_intervals(self) -> list[tuple[int, int]]:
        """本段内所有走访覆盖的并集；同段多场走访不能把长度重复累计。"""
        merged: list[tuple[int, int]] = []
        for a, b in sorted(self.covered_intervals):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        return merged

    @property
    def covered_m(self) -> int:
        return sum(b - a for a, b in self.union_intervals)

    @property
    def complete(self) -> bool:
        return any(a == 0 and b == self.length and ok for a, b, ok, _ in self.covered)

    def overlap(self, other: 'SegmentState') -> int:
        """两期覆盖的并集之交；结果不超过本段长度。"""
        return sum(max(0, min(b1, b2) - max(a1, a2))
                   for a1, b1 in self.union_intervals
                   for a2, b2 in other.union_intervals)


@dataclass
class SeasonBank:
    season: str
    bank: str
    segments: dict[str, SegmentState]
    observations: list[Observation]
    unresolved: list[Unresolved]

    def cites(self) -> tuple[str, ...]:
        out: tuple[str, ...] = ()
        for s in self.segments.values():
            out = tuple(dict.fromkeys(out + s.citations))
        for o in self.observations:
            out = tuple(dict.fromkeys(out + (o.note_id,) + o.mentions))
        return out

    def item_observations(self) -> list[Observation]:
        return [o for o in self.observations if o.unit_kind == UNIT_ITEM and o.value is not None]

    def weight_observations(self) -> list[Observation]:
        return [o for o in self.observations if o.unit_kind == UNIT_WEIGHT and o.value is not None]


def build_states(
    records: Records, observations: list[Observation],
    unresolved: list[Unresolved], parses: dict[str, NoteParse],
) -> dict[tuple[str, str], SeasonBank]:
    states: dict[tuple[str, str], SeasonBank] = {}

    def ensure(season: str, bank: str) -> SeasonBank:
        key = (season, bank)
        if key not in states:
            segs = {seg.id: SegmentState(seg.id, bank, season, seg.length)
                    for seg in records.segments_of(bank)}
            states[key] = SeasonBank(season, bank, segs, [], [])
        return states[key]

    for visit in records.visits:
        for bank in {s.bank for s in records.segments}:
            ensure(visit.season, bank)

    for parse in parses.values():
        for claim in parse.coverage:
            state = ensure(parse.season, claim.bank)
            for seg, a, b in records.segments_in(claim.bank, claim.lo, claim.hi):
                s = state.segments[seg.id]
                s.covered.append((a, b, claim.complete, parse.note_id))
                s.citations = tuple(dict.fromkeys(s.citations + (parse.note_id,)))

    for obs in observations:
        banks = ['左岸', '右岸'] if obs.bank == '两岸' else [obs.bank]
        for b in banks:
            ensure(obs.season, b).observations.append(obs)
        if obs.unit_kind == UNIT_WEIGHT:
            # 重量只挂到该秤所称范围覆盖到的段；整岸合计/两场合称不能被
            # 当作每一段的单独重量，也不能落到没去过的段上。
            hit_segs = set(_observation_segments(obs, records))
            for b in banks:
                state = ensure(obs.season, b)
                for s in state.segments.values():
                    if not s.covered or (hit_segs and s.seg_id not in hit_segs):
                        continue
                    if obs.status == 'joint' and s.weight_status == 'no_data':
                        s.weight_status, s.weight_note = 'joint', obs.evidence
                    elif obs.status == 'bank_total' and s.weight_status == 'no_data':
                        s.weight_status = 'bank_total'
                        s.weight_note = obs.evidence

    # 逐段件的归属：确认未见记零；有独立记录记精确；跨段合计只记上限
    for obs in observations:
        if obs.unit_kind != UNIT_ITEM:
            continue
        state = ensure(obs.season, obs.bank)
        for seg_id in obs.seg_ids:
            s = state.segments.get(seg_id)
            if not s:
                continue
            s.citations = tuple(dict.fromkeys(s.citations + (obs.note_id,) + obs.mentions))
            if obs.status == 'zero':
                if s.item_status == 'no_data':
                    s.item_status, s.item_value = 'zero', 0.0
                    s.item_note, s.item_obs = obs.evidence, (obs.obs_id,)
                elif s.item_status in ('exact', 'zero') and s.item_value:
                    s.item_status = 'conflict'
                    s.item_obs = tuple(dict.fromkeys(s.item_obs + (obs.obs_id,)))
                    s.item_note = (s.item_note + '；' + obs.evidence +
                                   '（同段同季记录不同，不能无声取一个）')
            elif obs.status == 'exact':
                if s.item_status == 'no_data':
                    s.item_status, s.item_value = 'exact', obs.value
                    s.item_upper = obs.value
                    s.item_note, s.item_obs = obs.evidence, (obs.obs_id,)
                elif s.item_status == 'zero':
                    # 同季另一场在同一段记了正数：不能把 0 悄悄改成正数，
                    # 也不能反过来；交给分析员按场次核对。
                    s.item_status = 'conflict'
                    s.item_value = None
                    s.item_obs = tuple(dict.fromkeys(s.item_obs + (obs.obs_id,)))
                    s.item_note = (s.item_note + '；' + obs.evidence +
                                   '（同段同季另一场记为 0，两场不能直接合并）')
                elif s.item_status == 'exact' and s.item_value != obs.value:
                    s.item_status = 'conflict'
                    s.item_value = None
                    s.item_obs = tuple(dict.fromkeys(s.item_obs + (obs.obs_id,)))
                    s.item_note = (s.item_note + '；' + obs.evidence +
                                   '（同段同季多场记录不同，不能无声取一个）')
            elif obs.status == 'group' and s.item_status == 'no_data':
                s.item_status, s.item_upper = 'group', obs.value
                s.item_note, s.item_obs = obs.evidence, (obs.obs_id,)

    for parse in parses.values():
        for neg in parse.negations:
            if neg.kind != 'not_weighed':
                continue
            state = ensure(parse.season, neg.bank)
            for seg_id in neg.seg_ids:
                s = state.segments.get(seg_id)
                if s and s.weight_status == 'no_data':
                    s.weight_status, s.weight_note = 'not_weighed', neg.text

    for item in unresolved:
        state = ensure(item.season, item.bank)
        state.unresolved.append(item)
        for seg_id in item.possible_seg_ids:
            s = state.segments.get(seg_id)
            if s and s.item_status == 'no_data':
                s.item_status = 'unresolved'
                s.item_note = item.text

    return states


# --------------------------------------------------------------------------
# 比较
# --------------------------------------------------------------------------

@dataclass
class Comparison:
    round_id: str
    metric: str
    bank: str
    season_a: str
    season_b: str
    status: str
    overlap_m: int
    a_value: float | None = None
    b_value: float | None = None
    a_upper: float | None = None
    b_upper: float | None = None
    direction: str = 'undetermined'
    detail: str = ''
    seg_ids: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _overlap_segments(
    records: Records, sa: SeasonBank, sb: SeasonBank, bank: str,
) -> tuple[list[str], int]:
    segs, total = [], 0
    for seg in records.segments_of(bank):
        m = sa.segments[seg.id].overlap(sb.segments[seg.id])
        if m:
            segs.append(seg.id)
            total += m
    return segs, total


def _rates_for(state: SeasonBank, seg_ids: list[str]) -> list[float | None]:
    rates: list[float | None] = []
    for seg_id in seg_ids:
        obs = [o for o in state.observations
               if o.unit_kind == UNIT_ITEM and seg_id in o.seg_ids]
        rates.append(next((o.rate for o in obs if o.rate is not None), None))
    return rates


def _rates_ok(criteria: Criteria, rates: list[float | None]) -> tuple[bool, str]:
    checks = [criteria.rate_ok(r) for r in rates]
    return all(ok for ok, _ in checks), '；'.join(why for _, why in checks)


def _group_obs(state: SeasonBank, seg_ids: list[str]) -> list[Observation]:
    out = []
    for obs in state.item_observations():
        if obs.status == 'group' and set(obs.seg_ids) & set(seg_ids):
            out.append(obs)
    return out


def compare_bank(
    records: Records, states: dict[tuple[str, str], SeasonBank], criteria: Criteria, bank: str,
) -> Comparison:
    season_a, season_b = criteria.seasons
    sa, sb = states[(season_a, bank)], states[(season_b, bank)]
    base = Comparison(criteria.round_id, criteria.metric, bank, season_a, season_b,
                      'no_data', 0)
    if criteria.metric == METRIC_WEIGHT:
        return _compare_weight(records, sa, sb, criteria, base)

    overlap_segs, overlap_m = _overlap_segments(records, sa, sb, bank)
    base = replace(base, overlap_m=overlap_m, seg_ids=tuple(overlap_segs))
    if overlap_m == 0:
        return replace(base, status='no_overlap',
                       detail=f'{bank}两期没有共同走过的范围，不能比较。')
    if overlap_m < criteria.min_overlap:
        return replace(base, status='insufficient_overlap',
                       detail=f'{bank}共同覆盖 {overlap_m} 米，少于口径要求的 {criteria.min_overlap} 米；'
                              '未走完的范围不计入有效覆盖。')

    exact, group, unresolved, no_data = [], [], [], []
    for seg_id in overlap_segs:
        a, b = sa.segments[seg_id], sb.segments[seg_id]
        if 'unresolved' in (a.item_status, b.item_status) or \
                'conflict' in (a.item_status, b.item_status):
            unresolved.append(seg_id)
        elif a.item_status in ('exact', 'zero') and b.item_status in ('exact', 'zero'):
            exact.append(seg_id)
        elif 'group' in (a.item_status, b.item_status):
            group.append(seg_id)
        else:
            no_data.append(seg_id)

    deps = [u for u in sa.unresolved + sb.unresolved
            if set(u.possible_seg_ids) & set(overlap_segs)]
    alts = tuple(u.text for u in deps)

    if not exact:
        if group:
            group_overlap = sum(sa.segments[s].overlap(sb.segments[s]) for s in group)
            return _bounded_comparison(records, sa, sb, criteria, base, group,
                                       overlap_segs, group_overlap, deps)
        if unresolved:
            return replace(
                base, status='unresolved',
                alternatives=tuple(
                    f'{u.note_id}：可能落在 {"、".join(u.possible_seg_ids)}'
                    for u in sa.unresolved + sb.unresolved
                ),
                detail='有观察但无法确定所属段，可能改变结论：' +
                       '；'.join(u.text for u in sa.unresolved + sb.unresolved),
            )
        return replace(base, status='no_data',
                       detail=f'{bank}共同范围没有可用的独立件数，空白不是零。')

    exact_overlap = sum(sa.segments[s].overlap(sb.segments[s]) for s in exact)
    if exact_overlap < criteria.min_overlap:
        return replace(
            base, status='insufficient_overlap',
            detail=f'{bank}可逐段比较的共同覆盖只有 {exact_overlap} 米'
                   f'（{"、".join(exact)}），少于口径要求的 {criteria.min_overlap} 米；'
                   f'另有 {"、".join(s for s in overlap_segs if s not in exact)} 只走过一部分或无对照，不并入比较。',
            seg_ids=tuple(exact), overlap_m=exact_overlap,
        )
    base = replace(base, overlap_m=exact_overlap, seg_ids=tuple(exact))
    a_sum = sum(sa.segments[s].item_value or 0 for s in exact)
    b_sum = sum(sb.segments[s].item_value or 0 for s in exact)
    cites: tuple[str, ...] = ()
    for s in exact:
        cites = tuple(dict.fromkeys(cites + sa.segments[s].citations + sb.segments[s].citations))

    ok_a, why_a = _rates_ok(criteria, _rates_for(sa, exact))
    ok_b, why_b = _rates_ok(criteria, _rates_for(sb, exact))
    if not (ok_a and ok_b):
        return replace(
            base, status='effort', a_value=a_sum, b_value=b_sum,
            direction='undetermined', citations=cites, alternatives=alts,
            detail=f'件数保留，但作业时长不可比：上期 {why_a}；本期 {why_b}；不据以宣布变化。',
        )

    weather_bad = []
    for obs in sa.observations + sb.observations:
        if obs.unit_kind == UNIT_ITEM and set(obs.seg_ids) & set(exact):
            ok, why = criteria.weather_ok(obs.weather)
            if not ok:
                weather_bad.append(f'{obs.note_id} {why}')
    if weather_bad:
        return replace(
            base, status='weather', a_value=a_sum, b_value=b_sum,
            direction='undetermined', citations=cites, alternatives=alts,
            detail='件数保留，但环境条件未达同口径：' + '；'.join(dict.fromkeys(weather_bad)),
        )

    conflicts = [seg_id for seg_id in overlap_segs
                 if 'conflict' in (sa.segments[seg_id].item_status,
                                   sb.segments[seg_id].item_status)]
    relevant_deps = [u for u in deps if set(u.possible_seg_ids) & set(exact)]
    if conflicts and not relevant_deps:
        notes_text = '；'.join(
            f'{seg_id}：{sa.segments[seg_id].item_note or sb.segments[seg_id].item_note}'
            for seg_id in conflicts
        )
        return replace(
            base, status='unresolved', direction='undetermined', citations=cites,
            alternatives=tuple(f'{seg_id} 同季多场记录不一致' for seg_id in conflicts),
            detail=f'{bank}同季多场在同一段留下不同记录，不能无声合并：{notes_text}',
        )
    if relevant_deps:
        return replace(
            base, status='unresolved', a_value=a_sum, b_value=b_sum,
            direction='undetermined', citations=cites,
            alternatives=tuple(
                f'{u.note_id}：另见瓶，没走近、不能确定在哪段；'
                f'可能落在 {"、".join(u.possible_seg_ids)}'
                for u in relevant_deps
            ),
            detail=(f'{bank}{"、".join(exact)} 的独立记录为上期 {a_sum:g} 件、本期 {b_sum:g} 件；'
                    '但另有未定归属的观察可能落入比较段，'
                    + _two_readings(bank, exact, a_sum, b_sum, relevant_deps) +
                    '。在归属确定前方向未定，不能宣布改善。'),
        )

    delta = b_sum - a_sum
    direction = 'decrease' if delta < 0 else ('increase' if delta > 0 else 'flat')
    detail = (f'{bank}{"、".join(exact)} 上期 {a_sum:g} 件、本期 {b_sum:g} 件；'
              f'共同覆盖 {overlap_m} 米，作业时长与天气均达公开口径。')
    return replace(base, status='valid', a_value=a_sum, b_value=b_sum,
                   direction=direction, detail=detail, citations=cites)


def _two_readings(bank: str, seg_ids: list[str], a_sum: float, b_sum: float,
                  deps: list[Unresolved]) -> str:
    return (f'若这些瓶落在 {("、".join(seg_ids))}，本期件数会高于 {b_sum:g}，'
            f'“上期 {a_sum:g} 件、本期 {b_sum:g} 件”的差额就不能成立；'
            f'若落在其他段，才可维持现有记录')


def _bounded_comparison(
    records: Records, sa: SeasonBank, sb: SeasonBank, criteria: Criteria,
    base: Comparison, group: list[str], overlap_segs: list[str],
    overlap_m: int, deps: list[Unresolved],
) -> Comparison:
    a_obs = [o for o in _group_obs(sa, group)]
    b_obs = [o for o in _group_obs(sb, group)]
    a_up = max((o.value for o in a_obs if o.value is not None), default=None)
    b_up = max((o.value for o in b_obs if o.value is not None), default=None)
    cites: tuple[str, ...] = ()
    for o in a_obs + b_obs:
        cites = tuple(dict.fromkeys(cites + (o.note_id,) + o.mentions))

    a_span = _obs_span(a_obs)
    b_span = _obs_span(b_obs)
    parts = [
        f'上期 {"、".join(group)} 的合计是 {a_up if a_up is not None else "未知"} 件'
        + (f'（原话覆盖 {a_span[0]}–{a_span[1]} 米）' if a_span else ''),
        f'本期合计是 {b_up if b_up is not None else "未知"} 件'
        + (f'（实际只覆盖 {b_span[0]}–{b_span[1]} 米）' if b_span else ''),
    ]
    detail = (
        f'{base.bank}合计所涉段（{"、".join(group)}）的共同覆盖为 {overlap_m} 米。'
        '两期都只有跨段合计、原话明确没有按段数，'
        '所以：合计值本身成立，但任何一段的份额都不可知，'
        '不能按平均摊到各段，也不能用不同长度的合计相除后宣布变化。' +
        '；'.join(parts) + '。'
    )
    same_span = bool(a_span and b_span and a_span == b_span)
    if not same_span:
        detail += ('两期合计覆盖的长度与位置不同，这不满足同努力、同范围可比；'
                   '本期未走完的范围不能补成零。')
    else:
        detail += '两期合计覆盖的范围相同，合计的差才是可核对的；但各段仍分不开。'
    detail += ('仍有把握的是：各期合计数与各自覆盖范围；'
               '各段件数的上限不超过所在合计，但不能当作段值。')
    relevant = [u for u in deps if set(u.possible_seg_ids) & set(group)]
    if relevant:
        detail += '另有未定归属的观察可能落入合计范围，方向仍未定。'
    return replace(
        base, status='bounded_unresolved' if relevant else 'bounded',
        a_upper=a_up, b_upper=b_up, overlap_m=overlap_m, seg_ids=tuple(group),
        direction='undetermined', detail=detail, citations=cites,
        alternatives=tuple(
            f'{u.note_id}：另见瓶，没走近、不能确定在哪段；'
            f'可能落在 {"、".join(u.possible_seg_ids)}'
            for u in relevant
        ),
    )


def _obs_span(observations: list[Observation]) -> tuple[int, int] | None:
    spans = [(o.lo, o.hi) for o in observations if o.lo is not None and o.hi is not None]
    if not spans:
        return None
    return min(a for a, _ in spans), max(b for _, b in spans)


def _observation_segments(obs: Observation, records: Records) -> tuple[str, ...]:
    if obs.seg_ids:
        return tuple(obs.seg_ids)
    if obs.lo is None or obs.hi is None:
        return ()
    banks = ('左岸', '右岸') if obs.bank == '两岸' else (obs.bank,)
    out: tuple[str, ...] = ()
    for bank in banks:
        out += tuple(seg.id for seg, _, _ in records.segments_in(bank, obs.lo, obs.hi))
    return tuple(dict.fromkeys(out))


def _compare_weight(
    records: Records, sa: SeasonBank, sb: SeasonBank, criteria: Criteria, base: Comparison,
) -> Comparison:
    issues: list[str] = []
    ok_a: list[Observation] = []
    ok_b: list[Observation] = []
    for state, bucket, label in ((sa, ok_a, base.season_a), (sb, ok_b, base.season_b)):
        for obs in state.weight_observations():
            if obs.shared or obs.status == 'joint':
                issues.append(f'{obs.note_id} 的 {obs.value:g} 公斤是两场合称'
                              f'（批号 {obs.batch or "未记"}），不能拆到{base.bank}。')
                continue
            ok, why = criteria.weather_ok(obs.weather)
            if not ok:
                issues.append(f'{obs.note_id} {why}，观察保留但不作比较。')
                continue
            ok, why = criteria.rate_ok(obs.rate)
            if not ok:
                issues.append(f'{obs.note_id} {why}。')
                continue
            bucket.append(obs)
    if not ok_a or not ok_b:
        missing = []
        if not ok_a:
            missing.append(f'{base.season_a} 没有符合口径的{base.bank}分段称重')
        if not ok_b:
            missing.append(f'{base.season_b} 没有符合口径的{base.bank}分段称重')
        return replace(
            base, status='no_weight_pair',
            detail='；'.join(missing + list(dict.fromkeys(issues))),
            alternatives=tuple(dict.fromkeys(issues)),
        )
    # 同岸同段：两次称重覆盖的段必须一致，且共同覆盖达到口径要求
    pairs = []
    for a_obs in ok_a:
        a_segs = set(_observation_segments(a_obs, records))
        for b_obs in ok_b:
            b_segs = set(_observation_segments(b_obs, records))
            if not a_segs or a_segs != b_segs:
                continue
            overlap = 0
            for seg_id in a_segs:
                overlap += sa.segments[seg_id].overlap(sb.segments[seg_id])
            if overlap < criteria.min_overlap:
                continue
            pairs.append((a_obs, b_obs, tuple(sorted(a_segs)), overlap))
    if not pairs:
        details = []
        for a_obs, b_obs in ((ok_a[0], ok_b[0]),):
            a_segs = _observation_segments(a_obs, records)
            b_segs = _observation_segments(b_obs, records)
            details.append(
                f'上期称重覆盖 {",".join(a_segs) or "未指明"}，本期覆盖 '
                f'{",".join(b_segs) or "未指明"}；不是同岸同段，'
                f'共同覆盖不足 {criteria.min_overlap} 米。'
            )
        return replace(
            base, status='no_weight_pair',
            detail='；'.join(details + list(dict.fromkeys(issues))),
            alternatives=tuple(dict.fromkeys(issues)),
        )
    a_obs, b_obs, seg_ids, overlap = pairs[0]
    delta = b_obs.value - a_obs.value
    return replace(
        base, status='valid', a_value=a_obs.value, b_value=b_obs.value,
        overlap_m=overlap, seg_ids=seg_ids,
        direction='decrease' if delta < 0 else ('increase' if delta > 0 else 'flat'),
        detail=f'同岸同段（{"、".join(seg_ids)}，共同覆盖 {overlap} 米）、'
               f'天气与作业时长可比的称重：上期 {a_obs.value:g} 公斤，'
               f'本期 {b_obs.value:g} 公斤。',
        citations=(a_obs.note_id, b_obs.note_id),
    )


def compare_all(records: Records, states: dict[tuple[str, str], SeasonBank]) -> list[Comparison]:
    out = []
    for round_row in records.rounds:
        criteria = criteria_for(round_row)
        for bank in sorted({s.bank for s in records.segments}):
            out.append(compare_bank(records, states, criteria, bank))
    return out


# --------------------------------------------------------------------------
# 汇总栏
# --------------------------------------------------------------------------

@dataclass
class CombinedTotal:
    status: str
    value: float | None
    detail: str
    parts: tuple[str, ...]
    citations: tuple[str, ...]
    label: str = ''

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def combined_weights(observations: list[Observation]) -> CombinedTotal:
    weights = [o for o in observations if o.unit_kind == UNIT_WEIGHT and o.value is not None]
    if not weights:
        return CombinedTotal('no_weight', None, '没有可相加的称重记录。', (), ())
    parts = tuple(
        f'{o.note_id} {o.value:g}{o.unit}'
        + (f'（批号 {o.batch}' if o.batch else '（')
        + ('，两场合称' if o.shared else '') + '）'
        for o in weights
    )
    visits = {o.visit_id for o in weights}
    batches = {o.batch for o in weights if o.batch}
    shared = any(o.shared or o.status == 'joint' for o in weights)
    if len(visits) > 1 or shared or (batches and len(batches) < len(weights)):
        return CombinedTotal(
            'not_additive', None,
            '这些称重记录并非同一批、同一范围、同一场次：' + '；'.join(parts) +
            '。共享批次无法分开，汇总栏不能把各栏上限或下限直接相加；'
            '相关小计与总计要一并替换，不能只改一个数。',
            parts, tuple(dict.fromkeys(o.note_id for o in weights)),
        )
    total = sum(o.value for o in weights)
    return CombinedTotal('valid', total, '同一批、同一范围、同一场次，可以相加。',
                         parts, tuple(dict.fromkeys(o.note_id for o in weights)))


def combine_intervals(rows: list[DisplayRow], observations: list[Observation]) -> CombinedTotal:
    """汇总栏：两栏区间若依赖同一批无法分开的数量，不能把上下限相加。"""
    if not rows:
        return CombinedTotal('no_rows', None, '没有可汇总的栏目。', (), ())
    parts = tuple(f'{r.season} {r.bank} {r.metric}'
                  f'（下限 {r.lower}，上限 {r.upper}）' for r in rows)
    cites = tuple(dict.fromkeys(c for r in rows for c in r.citations))

    # 同一条合称/同一批会同时出现在两栏的引用里；这类栏目共享数量，不能相加
    shared_ids: dict[str, set[str]] = {}
    for row in rows:
        want = UNIT_WEIGHT if row.metric == 'weight' else UNIT_ITEM
        for obs in observations:
            if obs.unit_kind != want:
                continue
            if obs.note_id in row.citations and (
                    obs.shared or obs.status == 'joint' or obs.batch):
                key = obs.batch or obs.obs_id
                shared_ids.setdefault(key, set()).add(row.bank)
    shared_across_rows = {k: banks for k, banks in shared_ids.items() if len(banks) > 1}
    if shared_across_rows:
        names = '、'.join(f'{k}（{"、".join(sorted(b))}）' for k, b in shared_across_rows.items())
        return CombinedTotal(
            'not_additive', None,
            '这些栏目的区间依赖同一批无法分开的数量：' + names +
            '。' + '；'.join(parts) +
            '。汇总栏不能把各栏上限或下限直接相加；若原始层级无法单独替换，'
            '小计与总计须一并替换。',
            parts, cites,
        )

    if any(r.lower is None and r.upper is None for r in rows):
        return CombinedTotal(
            'incomplete', None,
            '有一栏没有可核定的区间（未定归属或无资料），汇总栏无法给出合计；'
            '不能把空白当作零再相加。' + '；'.join(parts), parts, cites,
        )
    lower = sum(r.lower for r in rows) if all(r.lower is not None for r in rows) else None
    upper = sum(r.upper for r in rows) if all(r.upper is not None for r in rows) else None
    detail = '各栏区间不共享无法分开的数量，可以相加。'
    if lower is not None and upper is None:
        detail += '但只有下限可相加；上限因有栏未覆盖全岸而无法给出。'
    elif lower is not None and upper is not None and lower != upper:
        detail += '各栏上限不能当作点值；合计只能给区间。'
    return CombinedTotal('valid', upper if lower == upper else None, detail, parts, cites)


# --------------------------------------------------------------------------
# 全岸 / 分岸展示
# --------------------------------------------------------------------------

@dataclass
class DisplayRow:
    season: str
    bank: str
    metric: str
    value: float | None
    lower: float | None
    upper: float | None
    scope: str
    note: str
    citations: tuple[str, ...]

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _item_bank_row(records: Records, state: SeasonBank) -> DisplayRow:
    cites = state.cites()
    obs = state.item_observations()
    full = {s.seg_id for s in state.segments.values()}
    if not obs:
        return DisplayRow(state.season, state.bank, 'items', None, None, None,
                          'no_data', '无观察、无计量依据：不是零。', cites)
    if any(o.status == 'unresolved' for o in state.observations) or \
            any(s.item_status in ('unresolved', 'conflict')
                for s in state.segments.values()):
        return DisplayRow(state.season, state.bank, 'items', None, None, None,
                          'unresolved', '有观察但归属未定，不能给出岸级件数；'
                          '未定观察可能落入任一已到的段。', cites)

    covered = {seg for o in obs for seg in o.seg_ids}
    group_total = sum(o.value for o in obs if o.status == 'group' and o.value is not None)
    exact_total = sum(o.value for o in obs
                      if o.status in ('exact', 'zero') and o.value is not None)
    has_group = any(o.status == 'group' for o in obs)
    # 下限 = 独立记录之和；跨段合计本身就是一个确定的组值，可计入下限，
    # 但不能再把它当成每段各有一份。
    lower = exact_total + group_total
    if has_group:
        lower = exact_total + group_total
    if covered == full and not has_group:
        return DisplayRow(state.season, state.bank, 'items', lower, lower, lower,
                          'complete', '各段均有独立件数或确认未见。', cites)
    upper = lower if covered == full else None
    note = f'已核定的下限 {lower:g} 件'
    if has_group:
        note += '（含跨段合计，只在合计层计一次，不按段重复）'
    note += f'；仅 {len(covered)}/{len(full)} 段有观察；未观察部分无资料，不是零。'
    return DisplayRow(state.season, state.bank, 'items', None, lower, upper,
                      'partial', note, cites)


def _weight_bank_row(state: SeasonBank) -> DisplayRow:
    cites = state.cites()
    obs = state.weight_observations()
    if not obs:
        return DisplayRow(state.season, state.bank, 'weight', None, None, None,
                          'no_data', '没有可用单独称重：不是零。', cites)
    if any(o.shared or o.status == 'joint' for o in obs) and \
            not any(o.status == 'bank_total' and not o.shared for o in obs):
        best = next(o for o in obs if o.shared or o.status == 'joint')
        return DisplayRow(state.season, state.bank, 'weight', None, None, None,
                          'joint', f'本岸只有两场合称 {best.value:g} 公斤，不能分到单岸；'
                          '分岸展示不把合称拆成精确值。', cites)
    totals = [o for o in obs if o.status == 'bank_total' and not o.shared]
    if totals:
        value = totals[0].value if len({o.value for o in totals}) == 1 else None
        return DisplayRow(state.season, state.bank, 'weight', value, None, value,
                          'bank_total', '只有整岸合计称重，不能当作分段精确值。', cites)
    return DisplayRow(state.season, state.bank, 'weight', None, None, None,
                      'no_data', '没有可用单独称重：不是零。', cites)


def display(records: Records, states: dict[tuple[str, str], SeasonBank], mode: str) -> list[DisplayRow]:
    """mode='per_bank' 或 'whole_bank'。精确度与比较范围服从实际统计口径。"""
    bank_rows: list[DisplayRow] = []
    for (season, bank), state in sorted(states.items()):
        bank_rows.append(_item_bank_row(records, state))
        bank_rows.append(_weight_bank_row(state))
    if mode != 'whole_bank':
        return bank_rows
    whole: list[DisplayRow] = []
    for season in sorted({s for s, _ in states}):
        for metric in ('items', 'weight'):
            rows = [r for r in bank_rows if r.season == season and r.metric == metric]
            if len(rows) != 2:
                continue
            cites: tuple[str, ...] = ()
            for r in rows:
                cites = tuple(dict.fromkeys(cites + r.citations))
            joint = [o for (s, _), st in states.items() if s == season
                     for o in st.weight_observations() if o.shared or o.status == 'joint']
            if metric == 'weight' and joint:
                best = joint[0]
                whole.append(DisplayRow(
                    season, '两岸', metric, None, None, best.value, 'joint',
                    f'全岸只能给两场合称的合计 {best.value:g} 公斤，且这是批中合称，'
                    '不是全岸单独称重之和；切到分岸时两侧都不能分到单岸，'
                    '精确度随口径下降。', cites,
                ))
                continue
            if all(r.scope == 'complete' and r.value is not None for r in rows):
                total = sum(r.value for r in rows)
                whole.append(DisplayRow(season, '两岸', metric, total, total, total,
                                        'complete', '左右岸均有独立记录，可以相加。', cites))
            else:
                whole.append(DisplayRow(
                    season, '两岸', metric, None, None, None, 'mixed',
                    '左右岸精确度不一致，全岸不给点值；请切到分岸查看各自范围与引用。', cites,
                ))
    return whole + bank_rows
