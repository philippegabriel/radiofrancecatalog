"""Shared types for Radio France API data and generated records."""
from typing import NotRequired, TypedDict


type Timestamp = str | int | None
type GraphQLVariables = dict[str, str | int | None]


class TranscriptSegment(TypedDict):
    start: float
    end: float
    text: str
    speaker: NotRequired[str | None]


class Transcript(TypedDict):
    transcript: list[TranscriptSegment]


type Chunk = list[tuple[int, TranscriptSegment]]


class PodcastEpisode(TypedDict, total=False):
    id: str
    title: str | None
    url: str | None
    playerUrl: str | None
    created: Timestamp
    duration: int | None


class RadioFranceNode(TypedDict, total=False):
    id: str
    title: str | None
    url: str | None
    standFirst: str | None
    published_date: Timestamp
    podcastEpisode: PodcastEpisode | None


class GraphQLEdge(TypedDict):
    cursor: str | None
    node: RadioFranceNode | None


class GraphQLConnection(TypedDict, total=False):
    edges: list[GraphQLEdge] | None


class GraphQLData(TypedDict, total=False):
    shows: GraphQLConnection | None
    diffusionsOfShowByUrl: GraphQLConnection | None


class EpisodeRow(TypedDict):
    show: str
    id: str
    title: str
    description: str
    published_ts: Timestamp
    published_iso: str
    web_url: str
    podcast_title: str
    podcast_url: str
    player_url: str


class FictionRow(TypedDict):
    published_iso: str
    published_ts: str | int
    title: str
    description: str
    web_url: str
    podcast_title: str
    podcast_url: str
    player_url: str
    podcast_duration: str | int
    id: str
    show_title: str
    show_url: str
