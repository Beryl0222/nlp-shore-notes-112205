"""给社区的公开报告、旧栏目复核与更正单。

输出面向三类读者：
* 居民：有范围说明的观察、当前可比范围、可查证的引用；
* 分析员：未决差异（哪些合理归属会改变结论）；
* 发稿员：对应原栏目的更正，以及小计/总计是否要一并替换。

所有结论都能按手记编号回查原话。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .analysis import (
    METRIC_ITEMS, BagUnit, CombinedTotal, Comparison, DisplayRow, Observation,
    Unresolved, combine_intervals, display,
)
from .model import Records
from .parser import NoteParse


# --------------------------------------------------------------------------
# 范围说明
# --------------------------------------------------------------------------

@dataclass
class RangeNote:
    """某场某岸实际走到哪、哪里没走到、哪里称过重。"""

    season: str
    bank: str
    walked: tuple[str, ...]
    not_walked: tuple[str, ...]
    weighed: tuple[str, ...]
    not_weighed: tuple[str, ...]
    absent: tuple[str, ...]
    unresolved: tuple[str, ...]
    citations: tuple[str, ...]

    def lines(self) -> list[str]:
        out = []
        out.append(f'{self.season} {self.bank}：走过 {"、".join(self.walked) or "无"}')
        if self.not_walked:
            out.append('  没走到/没下去：' + '；'.join(self.not_walked))
        if self.weighed:
            out.append('  称过重：' + '；'.join(self.weighed))
        if self.not_weighed:
            out.append('  没过秤/无法称：' + '；'.join(self.not_weighed))
        if self.absent:
            out.append('  确认未见：' + '；'.join(self.absent))
        if self.unresolved:
            out.append('  归属未定：' + '；'.join(self.unresolved))
        out.append('  依据：' + ('、'.join(self.citations) or '无'))
        return out


def range_notes(records: Records, parses: dict[str, NoteParse], states) -> list[RangeNote]:
    out: list[RangeNote] = []
    for (season, bank), state in sorted(states.items()):
        walked, not_walked, weighed, not_weighed, absent, unresolved = [], [], [], [], [], []
        cites: tuple[str, ...] = ()
        for parse in parses.values():
            if parse.season != season:
                continue
            for claim in parse.coverage:
                if claim.bank != bank:
                    continue
                label = (f'{parse.note_id} {claim.start_name}→{claim.end_name or "未指明"}'
                         f'（{claim.lo}–{claim.hi} 米）')
                walked.append(label + ('' if claim.complete else '，只到这里'))
                if not claim.complete:
                    reach = '未走完'
                    if claim.nominal_hi is not None:
                        reach += f'（本应到 {claim.nominal_hi} 米）'
                    not_walked.append(f'{parse.note_id}：{reach}')
                cites = tuple(dict.fromkeys(cites + (parse.note_id,)))
            if parse.visit_bank != bank:
                continue
            for neg in parse.negations:
                text = f'{parse.note_id}：{neg.text}'
                if neg.kind == 'not_walked':
                    not_walked.append(text)
                elif neg.kind == 'absent':
                    absent.append(text)
                elif neg.kind == 'not_weighed':
                    not_weighed.append(text)
            for point in parse.collection_points:
                not_weighed.append(f'{parse.note_id}：袋子在{point}，未在堤下称')
        for obs in state.weight_observations():
            if obs.bank == bank:
                weighed.append(f'{obs.note_id} {obs.value:g}{obs.unit}'
                               + (f'（称点：{obs.weigh_point}）' if obs.weigh_point else ''))
        for item in state.unresolved:
            unresolved.append(f'{item.note_id}：{item.text}')
            cites = tuple(dict.fromkeys(cites + (item.note_id,)))
        out.append(RangeNote(
            season, bank, tuple(dict.fromkeys(walked)), tuple(dict.fromkeys(not_walked)),
            tuple(dict.fromkeys(weighed)), tuple(dict.fromkeys(not_weighed)),
            tuple(dict.fromkeys(absent)), tuple(dict.fromkeys(unresolved)), cites,
        ))
    return out


# --------------------------------------------------------------------------
# 公开解释
# --------------------------------------------------------------------------

@dataclass
class PublicExplanation:
    season: str
    bank: str
    metric: str
    comparable_range_m: int | None
    comparable_segments: tuple[str, ...]
    status: str
    published: str
    citations: tuple[str, ...]
    quotes: tuple[str, ...]

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def public_explanations(
    records: Records, states, comparisons: list[Comparison],
    parses: dict[str, NoteParse], season: str,
) -> list[PublicExplanation]:
    out: list[PublicExplanation] = []
    for comparison in comparisons:
        if comparison.season_b != season:
            continue
        bank = comparison.bank
        state = states[(season, bank)]
        quotes: tuple[str, ...] = ()
        for note_id in comparison.citations:
            quotes = tuple(dict.fromkeys(quotes + (f'{note_id}：{records.note(note_id).text}',)))
        if comparison.status == 'valid':
            published = (
                f'与{comparison.season_a}相比，{bank}{"、".join(comparison.seg_ids)} '
                f'在共同走过的 {comparison.overlap_m} 米内，件数由 {comparison.a_value:g} 变为 '
                f'{comparison.b_value:g}。这是同范围、同作业时长的比较，不代表整条岸。'
            )
        elif comparison.status == 'unresolved' and comparison.a_value is not None:
            published = (
                f'{bank}{"、".join(comparison.seg_ids)} 的独立记录由 {comparison.a_value:g} 件'
                f'变为 {comparison.b_value:g} 件；但 ' + '；'.join(comparison.alternatives) +
                '。若落在比较段，差额就不成立。以上为分析员可见的未决差异，'
                '对外只能说“方向未定”，不能宣布改善。'
            )
        elif comparison.status in ('bounded', 'bounded_unresolved'):
            a = f'{comparison.a_upper:g}' if comparison.a_upper is not None else '未知'
            b = f'{comparison.b_upper:g}' if comparison.b_upper is not None else '未知'
            total_len = sum(records.segment(s).length for s in comparison.seg_ids)
            published = (
                f'与{comparison.season_a}相比，{bank}{"、".join(comparison.seg_ids)} '
                f'两期合计数为上期 {a} 件、本期 {b} 件，但原话没有按段数，'
                f'各段份额不可知，不能宣布下降或上升。'
                f'这些段共 {total_len} 米，两期共同覆盖 '
                f'{comparison.overlap_m} 米；未走完的部分不计入。'
            )
        elif comparison.status == 'no_weight_pair':
            published = '没有同岸同段、同一口径的单独称重，重量不做季节比较。'
        elif comparison.status == 'effort':
            published = '件数保留，但作业时长不可比，不能据此宣布变化。'
        else:
            published = comparison.detail
        out.append(PublicExplanation(
            season=season, bank=bank, metric=comparison.metric,
            comparable_range_m=comparison.overlap_m,
            comparable_segments=comparison.seg_ids,
            status=comparison.status, published=published,
            citations=comparison.citations, quotes=quotes,
        ))
    return out


# --------------------------------------------------------------------------
# 旧栏目复核
# --------------------------------------------------------------------------

@dataclass
class ClaimCheck:
    claim: str
    status: str        # holds / loses_basis / needs_range / cannot_verify
    reason: str
    citations: tuple[str, ...]


@dataclass
class BulletinReview:
    bulletin_id: str
    part: str
    text: str
    articles: tuple[str, ...]
    cited_notes: tuple[str, ...]
    claims: tuple[ClaimCheck, ...]
    verdict: str
    range_explanation: str
    erratum: str | None
    replace_together: tuple[str, ...]
    withdraw_citations: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            **self.__dict__,
            'claims': [dict(c.__dict__) for c in self.claims],
        }


def _has_independent_items(records: Records, states, note_ids: tuple[str, ...],
                           season: str | None = None) -> bool:
    for (s, bank), state in states.items():
        if season and s != season:
            continue
        for obs in state.item_observations():
            if obs.note_id in note_ids and obs.status in ('exact', 'zero') and not obs.unresolved:
                return True
    return False


def review_bulletin(
    records: Records, states, comparisons: list[Comparison],
    parses: dict[str, NoteParse], bags: list[BagUnit], bulletin_id: str,
) -> BulletinReview:
    bulletin = records.bulletin(bulletin_id)
    checks: list[ClaimCheck] = []
    replace_together: tuple[str, ...] = ()
    withdraw: tuple[str, ...] = ()
    erratum = None

    text = bulletin.text

    if '重量' in text or '公斤' in text or '称重' in text:
        seen: dict[str, object] = {}
        for (s, b), st in states.items():
            for o in st.weight_observations():
                seen.setdefault(o.obs_id, o)
        weights = list(seen.values())
        bank_totals = [o for o in weights if o.status == 'bank_total']
        joints = [o for o in weights if o.status == 'joint' or o.shared]
        per_seg_claim = '分别称重' in text or '各段' in text
        if per_seg_claim:
            checks.append(ClaimCheck(
                '三段分别称重', 'loses_basis',
                '原话只有整岸合计与两场合称，没有任何一段的单独称重；'
                'N4 明确"没过秤"。',
                tuple(sorted({o.note_id for o in weights} | {'N4'})),
            ))
        if bank_totals or joints:
            parts = [f'{o.note_id} {o.value:g}{o.unit}'
                     f'（{("两场合称" if o.status == "joint" else "整岸合计")}'
                     f'，批号 {o.batch or "未记"}）' for o in weights]
            checks.append(ClaimCheck(
                '合计重量', 'loses_basis',
                '不能把整岸合计与两场合称直接相加：' + '；'.join(parts) +
                '。合计 14 公斤是把 8 公斤（左岸整岸）和 6 公斤（两场合称）相加得来的，'
                '两者覆盖范围与批次都不同。',
                tuple(sorted({o.note_id for o in weights})),
            ))
            replace_together = ('两岸小计', '总计')
            withdraw = tuple(sorted({o.note_id for o in weights}))
            erratum = (
                '原栏“三段分别称重合计14公斤”应替换为：'
                + '；'.join(parts) +
                '。这些数字不是三段分别称重，也不能相加；相关小计与总计须一并替换，'
                '不能只改其中一个数。'
            )
    elif '件' in text or '瓶' in text:
        if '下降' in text or '增加' in text or '少于' in text or '多于' in text:
            relevant = [c for c in comparisons if c.metric == METRIC_ITEMS]
            banks = {records.segment(a).bank for a in bulletin.articles
                     if a in {s.id for s in records.segments}}
            if banks:
                relevant = [c for c in relevant if c.bank in banks]
            names = '、'.join(f'{c.bank}:{c.status}' for c in relevant)
            checks.append(ClaimCheck(
                '件数变化', 'cannot_verify',
                f'两期覆盖范围与分段口径不同（复核结果 {names}）；'
                '原栏没有写明是哪一段、哪几米，不能据以宣布下降。',
                bulletin.citations,
            ))
            erratum = _item_erratum(records, relevant, text)

    cites_ok = all(nid in {n.id for n in records.notes} for nid in bulletin.citations)
    if not cites_ok:
        checks.append(ClaimCheck('引用可查', 'cannot_verify', '引用了不存在的手记。', ()))

    verdict = '保留原结论' if all(c.status == 'holds' for c in checks) and checks else '需更正'
    return BulletinReview(
        bulletin_id=bulletin.id, part=bulletin.part, text=text,
        articles=bulletin.articles, cited_notes=bulletin.citations,
        claims=tuple(checks), verdict=verdict,
        range_explanation=_range_explanation(records, states, comparisons, bulletin),
        erratum=erratum, replace_together=replace_together,
        withdraw_citations=withdraw,
    )


def _item_erratum(records: Records, relevant: list[Comparison], text: str) -> str:
    if not relevant:
        return ('原栏“' + text + '”没有可核对的比较记录；应撤下，'
                '待同类口径补充后再发。')
    parts = []
    for c in relevant:
        if c.status in ('bounded', 'bounded_unresolved'):
            a = f'{c.a_upper:g}' if c.a_upper is not None else '未知'
            b = f'{c.b_upper:g}' if c.b_upper is not None else '未知'
            segs = '、'.join(c.seg_ids) or c.bank
            parts.append(
                f'{segs} 两期都只有跨段合计（上期 {a} 件、本期 {b} 件），'
                f'没有可分的子段，不能宣布下降；两期共同覆盖仅 {c.overlap_m} 米，'
                '对外应写“合计值可核对，分段变化未定”'
            )
        elif c.status == 'insufficient_overlap':
            parts.append(
                f'{c.bank}可比较的共同覆盖只有 {c.overlap_m} 米，'
                '少于公开口径要求的重叠；不能宣布变化'
            )
        elif c.status == 'unresolved':
            if c.a_value is not None:
                alts = '；'.join(c.alternatives) or '另有未定归属的观察'
                parts.append(
                    f'{c.bank}{",".join(c.seg_ids)} 的独立记录为上期 {c.a_value:g} 件、'
                    f'本期 {c.b_value:g} 件，但 {alts}，会把差额改掉；'
                    '应写“方向未定”，不能宣布改善'
                )
            else:
                parts.append(f'{c.bank}另有未定归属的观察，不能宣布下降')
        elif c.status == 'valid':
            parts.append(
                f'{c.bank}{",".join(c.seg_ids)} 在共同 {c.overlap_m} 米内为 '
                f'{c.a_value:g}→{c.b_value:g} 件，应写明范围与作业时长'
            )
        else:
            parts.append(f'{c.bank}的比较状态为 {c.status}，不支持原栏说法')
    return ('原栏“' + text + '”应替换为：' + '；'.join(parts) +
            '。并注明可查证的手记编号，以及原栏所称范围与实际共同覆盖范围的区别。')


def _range_explanation(records: Records, states, comparisons, bulletin) -> str:
    lines = []
    want_weight = '重量' in bulletin.text or '公斤' in bulletin.text or '称重' in bulletin.text
    article_banks = {records.segment(a).bank for a in bulletin.articles
                     if a in {s.id for s in records.segments}}
    for bank in sorted(article_banks or {s.bank for s in records.segments}):
        relevant = [c for c in comparisons
                    if c.bank == bank and (c.metric == 'weight') == want_weight]
        for c in relevant:
            lines.append(
                f'{bank}：共同覆盖 {c.overlap_m} 米（{",".join(c.seg_ids) or "无"}），'
                f'状态 {c.status}'
            )
    segs = []
    for seg_id in bulletin.articles:
        try:
            seg = records.segment(seg_id)
        except KeyError:
            continue
        segs.append(f'{seg_id}={seg.bank}{seg.start}–{seg.end} 米')
    return ('原栏挂靠岸段：' + '、'.join(segs) + '。' if segs else '') + '；'.join(lines)


# --------------------------------------------------------------------------
# 补交清单后的复核
# --------------------------------------------------------------------------

@dataclass
class Reassessment:
    supplement: str
    still_holds: tuple[str, ...]
    loses_basis: tuple[str, ...]
    unchanged_columns: tuple[str, ...]
    erratum_targets: tuple[str, ...]
    note: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def reassess_after_supplement(
    records: Records, states, comparisons: list[Comparison],
    parses: dict[str, NoteParse], bags: list[BagUnit],
) -> Reassessment:
    """N8 补交清单确认两段的袋子原在同一批（柳17）称重后的复核。

    只据资料里已有的批次关系判断，不凭补交就重新拆分重量。
    """
    holds: list[str] = []
    loses: list[str] = []
    unchanged: list[str] = []
    flagged: list[str] = []

    joint = [o for (s, b), st in states.items()
             for o in st.weight_observations() if o.status == 'joint']
    batch_bags = [b for b in bags if b.batch]
    if joint:
        o = joint[0]
        holds.append(
            f'{o.note_id} 的 {o.value:g} 公斤仍是两场合称总量（批号 {o.batch or "未记"}），'
            '作为“合计”可以继续引用，但不能拆到左岸或右岸。'
        )
        loses.append('把该合称当作某一岸或某一段单独重量的说法失去依据。')
    if batch_bags:
        held = [b for b in batch_bags if b.folded_into]
        if held:
            b = held[0]
            holds.append(
                f'袋数仍然成立：批号 {b.batch} 的 {b.value:g} 袋是同一袋，'
                f'手记 {"、".join(b.mentions)} 重复提到也只算一袋；'
                f'它已并入 {b.folded_into} 的称重，不能再另计一次重量。'
            )
            loses.append('把同一袋按两队各记一次、从而算成两袋或两份重量的说法失去依据。')
        unlinked = [b for b in batch_bags if not b.folded_into]
        for b in unlinked:
            flagged.append(
                f'批号 {b.batch} 的 {b.value:g} 袋（手记 {"、".join(b.mentions)}）'
                '只有袋数与批次，没有单独称重值；它不能并入任何一栏的重量。'
            )

    per_seg = [b for b in records.bulletins
               if '分别称重' in b.text or '各段' in b.text]
    if per_seg:
        loses.append(
            '“三段分别称重”的说法失去依据：原话只有整岸合计与两场合称，'
            '补交只补了批次来源，没有补出分段重量。'
        )
        erratum_parts = tuple(b.part for b in per_seg)
        flagged.append('须一并替换的栏目：' + '、'.join(erratum_parts) + ' 及其小计/总计。')

    # 件数原本独立的一栏，补交不涉及计数，可继续引用
    independent = []
    for note_id in ('N1', 'N3', 'N5'):
        if _has_independent_items(records, states, (note_id,)):
            independent.append(note_id)
    if independent:
        unchanged.append(
            '件数栏可以继续引用：' + '、'.join(independent) +
            ' 的件数原本就有独立记录，补交只涉及袋子与称重批次，没有改动这些计数；'
            '若原始层级无法单独替换，明示相关小计与总计一并替换。'
        )

    note = (
        '补交确认两段的袋子原在同一批（柳17）称重，但只补了批次来源与袋子归属，'
        '没有新增可分的分段重量。仍成立的是：同一袋只算一袋、合称总量只能作合计引用、'
        '件数原本独立的一栏可继续引用。失去依据的是：把合称拆成单岸或单段、'
        '把同一袋重复计数、以及“三段分别称重”的说法。相关栏目的小计与总计须一并替换。'
    )
    return Reassessment(
        supplement='N8', still_holds=tuple(holds), loses_basis=tuple(loses),
        unchanged_columns=tuple(unchanged), erratum_targets=tuple(flagged), note=note,
    )


# --------------------------------------------------------------------------
# 发布人员：按原栏目领取更正
# --------------------------------------------------------------------------

@dataclass
class ErratumPacket:
    part: str
    bulletin_ids: tuple[str, ...]
    original: tuple[str, ...]
    replacement: tuple[str, ...]
    dependent_totals: tuple[str, ...]
    still_usable: tuple[str, ...]
    must_replace_together: bool
    note: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _usable_for_part(part: str, reassessment: Reassessment, states) -> tuple[str, ...]:
    """件数栏只在确有独立记录可回查时，才说哪一部分仍可引用。"""
    if part not in ('岸段件数', '右岸件数'):
        return ()
    bank = '右岸' if part == '右岸件数' else '左岸'
    exact_notes: dict[str, set[str]] = {}
    group_notes: dict[str, set[str]] = {}
    for (season, b), state in states.items():
        if b != bank:
            continue
        for obs in state.item_observations():
            if obs.status in ('exact', 'zero'):
                exact_notes.setdefault(obs.note_id, set()).update(obs.seg_ids)
            elif obs.status == 'group':
                group_notes.setdefault(obs.note_id, set()).update(obs.seg_ids)
    usable = []
    if exact_notes:
        detail = '、'.join(f'{nid}（{"、".join(sorted(segs))}）'
                          for nid, segs in sorted(exact_notes.items()))
        usable.append(f'{bank}有独立件数或确认未见的手记：{detail}，这些段仍可引用')
    if group_notes:
        detail = '、'.join(f'{nid}（{"、".join(sorted(segs))}）'
                          for nid, segs in sorted(group_notes.items()))
        usable.append(
            f'{bank}另有手记 {detail} 只给跨段合计，'
            '引用时必须连同"没有按段数"一起写，不能当作分段值'
        )
    return tuple(usable)


def erratum_packets(reviews: list[BulletinReview], reassessment: Reassessment,
                    states) -> list[ErratumPacket]:
    """按原栏目（part）归并更正，方便发稿员对应领取。"""
    by_part: dict[str, list[BulletinReview]] = {}
    for review in reviews:
        by_part.setdefault(review.part, []).append(review)

    packets: list[ErratumPacket] = []
    for part, group in by_part.items():
        originals = tuple(r.text for r in group)
        replacements = tuple(r.erratum for r in group if r.erratum)
        together = any(r.replace_together for r in group)
        dependents: tuple[str, ...] = ()
        for r in group:
            dependents = tuple(dict.fromkeys(dependents + r.replace_together))
        if together:
            dependents = tuple(dict.fromkeys(dependents + ('总计',)))
        dependents = tuple(d for d in dependents if d != part)
        usable: tuple[str, ...] = ()
        for r in group:
            for claim in r.claims:
                if claim.status == 'holds':
                    usable = tuple(dict.fromkeys(usable + (claim.claim,)))
        note = (
            '原始层级无法单独替换：' if together else ''
        ) + ('发布时须把该栏目及其小计、总计一并替换，不能只改一个数。'
             if together else '按更正稿替换本栏目即可；引用原话保留手记编号。')
        packets.append(ErratumPacket(
            part=part, bulletin_ids=tuple(r.bulletin_id for r in group),
            original=originals, replacement=replacements,
            dependent_totals=dependents,
            still_usable=_usable_for_part(part, reassessment, states),
            must_replace_together=together, note=note,
        ))
    return packets


# --------------------------------------------------------------------------
# 整份报告
# --------------------------------------------------------------------------

@dataclass
class Report:
    title: str
    season: str
    range_notes: tuple[RangeNote, ...]
    explanations: tuple[PublicExplanation, ...]
    unresolved: tuple[Unresolved, ...]
    display_rows: tuple[DisplayRow, ...]
    bag_units: tuple[BagUnit, ...]
    reviews: tuple[BulletinReview, ...]
    reassessment: Reassessment
    errata: tuple[ErratumPacket, ...] = ()
    totals: tuple[CombinedTotal, ...] = ()
    quotes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'title': self.title,
            'season': self.season,
            'range_notes': [
                {'season': r.season, 'bank': r.bank, 'walked': list(r.walked),
                 'not_walked': list(r.not_walked), 'weighed': list(r.weighed),
                 'not_weighed': list(r.not_weighed), 'absent': list(r.absent),
                 'unresolved': list(r.unresolved), 'citations': list(r.citations)}
                for r in self.range_notes
            ],
            'explanations': [e.to_dict() for e in self.explanations],
            'unresolved': [u.to_dict() for u in self.unresolved],
            'display_rows': [d.to_dict() for d in self.display_rows],
            'bag_units': [b.to_dict() for b in self.bag_units],
            'reviews': [r.to_dict() for r in self.reviews],
            'reassessment': self.reassessment.to_dict(),
            'errata': [e.to_dict() for e in self.errata],
            'totals': [t.to_dict() for t in self.totals],
            'quotes': self.quotes,
        }


def build_report(
    records: Records, parses: dict[str, NoteParse], states,
    observations: list[Observation], bags: list[BagUnit],
    unresolved: list[Unresolved], comparisons: list[Comparison],
    season: str, display_mode: str = 'per_bank',
) -> Report:
    reviews = [review_bulletin(records, states, comparisons, parses, bags, b.id)
               for b in records.bulletins]
    quotes = {n.id: n.text for n in records.notes}
    relevant_seasons = {season} | {c.season_a for c in comparisons if c.season_b == season}
    notes = [rn for rn in range_notes(records, parses, states)
             if rn.season in relevant_seasons]
    reassessment = reassess_after_supplement(records, states, comparisons, parses, bags)
    rows = display(records, states, display_mode)
    totals = []
    for season in sorted({v.season for v in records.visits}):
        for metric in ('items', 'weight'):
            same = [r for r in rows if r.season == season and r.bank != '两岸'
                    and r.metric == metric]
            if same:
                total = combine_intervals(same, observations)
                label = f'{season} {"瓶件数" if metric == "items" else "已称重量"}'
                totals.append(replace(total, label=label))
    return Report(
        title='柳涧净滩巡查公开报告（含范围说明与更正）',
        season=season,
        range_notes=tuple(notes),
        explanations=tuple(public_explanations(records, states, comparisons, parses, season)),
        unresolved=tuple(u for u in unresolved if u.season in relevant_seasons),
        display_rows=tuple(r for r in display(records, states, display_mode)
                           if r.season in relevant_seasons or r.season == '两岸'),
        bag_units=tuple(b for b in bags if b.season in relevant_seasons),
        reviews=tuple(reviews),
        reassessment=reassessment,
        errata=tuple(erratum_packets(reviews, reassessment, states)),
        totals=tuple(totals),
        quotes=quotes,
    )


def render_text(report: Report) -> str:
    """居民可读的纯文本；每条结论后附手记编号。"""
    lines = [report.title, '=' * len(report.title), '']
    lines.append('一、范围说明（走过哪里、哪里没走到、哪里称过重）')
    for note in report.range_notes:
        lines.extend('  ' + line for line in note.lines())
    lines.append('')
    lines.append('二、当前可比范围与公开解释')
    if not report.explanations:
        lines.append('  本季没有可比较的公开口径。')
    for exp in report.explanations:
        metric = '瓶件数' if exp.metric == METRIC_ITEMS else '已称重量'
        lines.append(f'  [{exp.bank}·{metric}] 可比范围 {exp.comparable_range_m} 米'
                     f'（{",".join(exp.comparable_segments) or "无"}）')
        lines.append(f'    {exp.published}')
        if exp.citations:
            lines.append('    可查：' + '、'.join(exp.citations))
    lines.append('')
    lines.append('三、未决差异（分析员用）')
    if not report.unresolved:
        lines.append('  无。')
    for item in report.unresolved:
        lines.append(f'  {item.note_id} {item.bank}：{item.text}')
        lines.append(f'    可能落入：{",".join(item.possible_seg_ids)}；{item.affects}')
    lines.append('')
    lines.append('四、袋数与批次（同一袋只算一袋）')
    for bag in report.bag_units:
        point = f'，称点 {bag.weigh_point}' if bag.weigh_point else ''
        lines.append(f'  {bag.season} {bag.bank} {bag.value:g} 袋'
                     f'（批号 {bag.batch or "未记"}，{bag.owner_team}{point}，'
                     f'手记 {",".join(bag.mentions)}）：{bag.note}')
    lines.append('')
    lines.append('五、旧栏目复核与更正')
    for review in report.reviews:
        lines.append(f'  [{review.bulletin_id} · {review.part}] {review.text}')
        lines.append(f'    结论：{review.verdict}')
        for claim in review.claims:
            lines.append(f'    - {claim.claim}：{claim.status}。{claim.reason}')
        lines.append(f'    范围：{review.range_explanation}')
        if review.erratum:
            lines.append(f'    更正稿：{review.erratum}')
        if review.replace_together:
            lines.append('    须一并替换：' + '、'.join(review.replace_together))
        if review.cited_notes:
            lines.append('    原栏引用（可回查）：' + '、'.join(review.cited_notes))
    lines.append('')
    lines.append('六、补交清单后的复核')
    for text in report.reassessment.still_holds:
        lines.append('  仍成立：' + text)
    for text in report.reassessment.loses_basis:
        lines.append('  失去依据：' + text)
    for text in report.reassessment.unchanged_columns:
        lines.append('  可继续引用：' + text)
    lines.append('  ' + report.reassessment.note)
    lines.append('')
    lines.append('七、汇总栏检查（不能把各栏上下限直接相加）')
    if not report.totals:
        lines.append('  无。')
    for total in report.totals:
        value = '—' if total.value is None else f'{total.value:g}'
        label = f'{total.label} ' if total.label else ''
        lines.append(f'  [{total.status}] {label}合计 {value}：{total.detail}')
    lines.append('')
    lines.append('八、发布人员：按原栏目领取的更正')
    for packet in report.errata:
        lines.append(f'  [{packet.part}] 关联旧栏 {",".join(packet.bulletin_ids)}')
        lines.append(f'    原文：{" / ".join(packet.original)}')
        for rep in packet.replacement:
            lines.append(f'    更正稿：{rep}')
        if packet.dependent_totals:
            lines.append('    连带替换：' + '、'.join(packet.dependent_totals))
        if packet.still_usable:
            lines.append('    仍可引用：' + '；'.join(packet.still_usable) +
                         '。补交只涉及袋子与称重批次。')
        lines.append('    ' + packet.note)
    lines.append('')
    lines.append('九、按实际口径展示')
    for row in report.display_rows:
        value = '—' if row.value is None else f'{row.value:g}'
        rng = ''
        if row.lower is not None or row.upper is not None:
            fmt = lambda v: f'{v:g}' if isinstance(v, (int, float)) else (v or '?')
            rng = f'（范围 {fmt(row.lower)}–{fmt(row.upper)}）'
        lines.append(f'  {row.season} {row.bank} {row.metric}：{value}{rng} [{row.scope}] {row.note}')
    return '\n'.join(lines)
