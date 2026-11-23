"""资料表与实体段模型。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[1] / 'data' / 'records.json'


@dataclass(frozen=True)
class Segment:
    id: str
    bank: str
    start: int
    end: int
    aliases: tuple[str, ...] = ()

    @property
    def length(self) -> int:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {
            'id': self.id, 'bank': self.bank, 'from_m': self.start,
            'to_m': self.end, 'aliases': list(self.aliases),
        }


@dataclass(frozen=True)
class Visit:
    id: str
    season: str
    at: str
    team: str
    bank: str
    effort_minutes: int | None
    weather: str

    def to_dict(self) -> dict:
        return {
            'id': self.id, 'season': self.season, 'at': self.at,
            'team': self.team, 'bank': self.bank,
            'effort_minutes': self.effort_minutes, 'weather': self.weather,
        }


@dataclass(frozen=True)
class Note:
    id: str
    visit: str
    writer: str
    text: str


@dataclass(frozen=True)
class Round:
    id: str
    seasons: tuple[str, ...]
    metric: str
    allowed_weather: tuple[str, ...]
    effort: str
    min_overlap: int
    minutes_per_100m: int = 20
    tolerance_minutes: int = 2

    def to_dict(self) -> dict:
        return {
            'id': self.id, 'seasons': list(self.seasons), 'metric': self.metric,
            'allowed_weather': list(self.allowed_weather), 'effort': self.effort,
            'min_overlap': self.min_overlap,
        }


@dataclass(frozen=True)
class Bulletin:
    id: str
    part: str
    articles: tuple[str, ...]
    citations: tuple[str, ...]
    text: str

    def to_dict(self) -> dict:
        return {
            'id': self.id, 'part': self.part, 'articles': list(self.articles),
            'citations': list(self.citations), 'text': self.text,
        }


@dataclass(frozen=True)
class Records:
    segments: tuple[Segment, ...]
    visits: tuple[Visit, ...]
    notes: tuple[Note, ...]
    rounds: tuple[Round, ...]
    bulletins: tuple[Bulletin, ...]
    source: Path | None = None

    # ---- lookup -----------------------------------------------------------
    def segment(self, seg_id: str) -> Segment:
        return self._index('segments')[seg_id]

    def visit(self, visit_id: str) -> Visit:
        return self._index('visits')[visit_id]

    def note(self, note_id: str) -> Note:
        return self._index('notes')[note_id]

    def round(self, round_id: str) -> Round:
        return self._index('rounds')[round_id]

    def bulletin(self, bulletin_id: str) -> Bulletin:
        return self._index('bulletins')[bulletin_id]

    def _index(self, table: str) -> dict:
        return {row.id: row for row in getattr(self, table)}

    def segments_of(self, bank: str) -> list[Segment]:
        return sorted((s for s in self.segments if s.bank == bank), key=lambda s: s.start)

    def segments_in(self, bank: str, lo: int, hi: int) -> list[tuple[Segment, int, int]]:
        """返回与 [lo, hi) 有交集的段及其交集。"""
        hits = []
        for seg in self.segments:
            if seg.bank != bank:
                continue
            a, b = max(seg.start, lo), min(seg.end, hi)
            if b > a:
                hits.append((seg, a, b))
        return hits


def _number(value) -> int | None:
    return None if value is None else int(value)


def load_records(path: Path | str = DATA_PATH) -> Records:
    import json

    path = Path(path)
    raw = json.loads(path.read_text(encoding='utf-8'))
    segments = tuple(
        Segment(str(r['id']), r['bank'], _number(r['from_m']), _number(r['to_m']),
                tuple(r.get('aliases') or ()))
        for r in raw['segments']
    )
    visits = tuple(
        Visit(str(r['id']), r['season'], r['at'], r['team'], r['bank'],
              _number(r.get('effort_minutes')), r['weather'])
        for r in raw['visits']
    )
    notes = tuple(Note(str(r['id']), str(r['visit']), r['writer'], r['text']) for r in raw['notes'])
    rounds = tuple(
        Round(str(r['id']), tuple(r['seasons']), r['metric'], tuple(r['allowed_weather']),
              r['effort'], _number(r['min_overlap']))
        for r in raw['rounds']
    )
    bulletins = tuple(
        Bulletin(str(r['id']), r['part'], tuple(r['articles']), tuple(r['citations']), r['text'])
        for r in raw['bulletins']
    )
    return Records(segments, visits, notes, rounds, bulletins, path)
