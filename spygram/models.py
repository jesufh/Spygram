"""
spygram.models
~~~~~~~~~~~~~~

Domain data models, pagination state containers, and robust parsers for Instagram entities.

Provides typed, immutable representations for posts, stories, reels,
highlights, profiles, media resources, and pagination cursors, isolating API
schema changes from the rest of the application.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Generic, TypeVar

T = TypeVar("T")

_SLUG_SANITIZE_RE = re.compile(r'[\\/:*?"<>|]')
"""Regular expression matching characters prohibited in filenames."""

_SLUG_SPACES_RE = re.compile(r"[\s_]+")
"""Regular expression matching consecutive whitespace and underscores."""

_HASHTAG_RE = re.compile(r"#([\w_]+)")
"""Regular expression matching hashtag tokens in captions."""

_MENTION_RE = re.compile(r"@([\w_.]+)")
"""Regular expression matching user mentions in captions."""


def slugify(text: str) -> str:
    """
    Sanitize text to create a safe filesystem directory or file name.

    :param text: Raw input string.
    :type text: str
    :return: Cleaned and normalized string.
    :rtype: str
    """
    cleaned = _SLUG_SANITIZE_RE.sub("", text.strip())
    return _SLUG_SPACES_RE.sub("_", cleaned).strip("_") or "unnamed"


def extract_hd_profile_pic_url(data: dict[str, Any], allow_standard_fallback: bool = True) -> str:
    """
    Extract the highest resolution profile picture URL from a user or media dictionary.

    Inspects both ``hd_profile_pic_url_info`` and ``hd_profile_pic_versions``
    structures commonly present in user and post payloads.

    :param data: JSON payload containing user or owner data.
    :type data: dict[str, Any]
    :param allow_standard_fallback: If True, falls back to standard profile_pic_url if HD not found.
    :type allow_standard_fallback: bool
    :return: High-resolution image URL, or empty string if not found.
    :rtype: str
    """
    if not isinstance(data, dict):
        return ""

    user = data.get("user") if isinstance(data.get("user"), dict) else (
        data.get("owner") if isinstance(data.get("owner"), dict) else data
    )
    if not isinstance(user, dict) or not user:
        return ""

    hd_info = user.get("hd_profile_pic_url_info")
    if isinstance(hd_info, dict) and hd_info.get("url"):
        return str(hd_info["url"])

    hd_versions = user.get("hd_profile_pic_versions")
    if isinstance(hd_versions, list) and hd_versions:
        sorted_versions = sorted(
            [v for v in hd_versions if isinstance(v, dict) and "url" in v],
            key=lambda x: x.get("height", 0) * x.get("width", 0),
            reverse=True,
        )
        if sorted_versions:
            return str(sorted_versions[0]["url"])

    if user.get("profile_pic_url_hd"):
        return str(user["profile_pic_url_hd"])

    if allow_standard_fallback:
        return str(user.get("profile_pic_url") or "")

    return ""


@dataclass(slots=True, frozen=True)
class PaginationState:
    """
    Serializable state snapshot for resumable asynchronous pagination.

    :param cursor: Opaque pagination cursor or identifier string.
    :type cursor: str | None
    :param page_index: Zero-indexed counter of fetched pages.
    :type page_index: int
    :param total_yielded: Total number of items yielded across all pages.
    :type total_yielded: int
    :param extra_params: Arbitrary additional state parameters.
    :type extra_params: dict[str, Any]
    """

    cursor: str | None = None
    page_index: int = 0
    total_yielded: int = 0
    extra_params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize pagination state to a JSON-compatible dictionary.

        :return: State dictionary mapping.
        :rtype: dict[str, Any]
        """
        return {
            "cursor": self.cursor,
            "page_index": self.page_index,
            "total_yielded": self.total_yielded,
            "extra_params": self.extra_params,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaginationState:
        """
        Instantiate state from a serialized dictionary representation.

        :param data: Serialized mapping.
        :type data: dict[str, Any]
        :return: Reconstructed PaginationState instance.
        :rtype: PaginationState
        """
        return cls(
            cursor=data.get("cursor"),
            page_index=int(data.get("page_index", 0)),
            total_yielded=int(data.get("total_yielded", 0)),
            extra_params=dict(data.get("extra_params", {})),
        )


@dataclass(slots=True, frozen=True)
class PageResult(Generic[T]):
    """
    Standardized page result container for REST and GraphQL feed queries.

    :param items: List of domain items extracted from the page.
    :type items: list[T]
    :param next_cursor: Next pagination cursor string, or None if end of feed.
    :type next_cursor: str | None
    :param has_more: True if additional pages remain available.
    :type has_more: bool
    """

    items: list[T]
    next_cursor: str | None
    has_more: bool


@dataclass(slots=True, frozen=True)
class MediaResource:
    """
    Represents an individual downloadable binary resource (image or video).

    :param url: Direct CDN download URL.
    :type url: str
    :param is_video: True if the resource is an MP4 video, False for JPG.
    :type is_video: bool
    :param width: Resource width in pixels.
    :type width: int
    :param height: Resource height in pixels.
    :type height: int
    :param suffix: Optional filename suffix for album child elements (e.g., '_1', '_2').
    :type suffix: str
    """

    url: str
    is_video: bool
    width: int = 0
    height: int = 0
    suffix: str = ""

    @property
    def extension(self) -> str:
        """
        Suggested file extension based on media type.

        :return: 'mp4' for videos, 'jpg' for photos.
        :rtype: str
        """
        return "mp4" if self.is_video else "jpg"


@dataclass(slots=True, frozen=True)
class MusicInfo:
    """
    Audio metadata associated with a post or story.

    :param title: Song or audio clip title.
    :type title: str
    :param artist: Artist or original creator name.
    :type artist: str
    """

    title: str
    artist: str


@dataclass(slots=True, frozen=True)
class LocationInfo:
    """
    Geographical location information tagged on a post.

    :param id: Instagram location identifier.
    :type id: str
    :param name: Human-readable location name.
    :type name: str
    """

    id: str
    name: str


@dataclass(slots=True, frozen=True)
class MediaItem:
    """
    Unified domain model representing any Instagram post, reel, story, or tagged item.

    :param id: Unique numeric Instagram media ID.
    :type id: str
    :param code: Alphanumeric shortcode (e.g., 'Dbo00HOmnjr').
    :type code: str
    :param taken_at: UTC timestamp when the media was published.
    :type taken_at: datetime
    :param owner_username: Username of the author or publishing account.
    :type owner_username: str
    :param caption: Text caption associated with the media.
    :type caption: str
    :param media_type: Normalized type string ('photo', 'video', 'album').
    :type media_type: str
    :param resources: List of downloadable media resources.
    :type resources: list[MediaResource]
    :param like_count: Total likes count.
    :type like_count: int
    :param comment_count: Total comments count.
    :type comment_count: int
    :param play_count: Video views or play count.
    :type play_count: int
    :param expiring_at: UTC expiration timestamp for ephemeral stories.
    :type expiring_at: datetime | None
    :param location: Tagged geolocation metadata, if available.
    :type location: LocationInfo | None
    :param music: Attached audio metadata, if available.
    :type music: MusicInfo | None
    :param hashtags: List of parsed hashtag strings.
    :type hashtags: list[str]
    :param mentions: List of mentioned usernames and tagged accounts.
    :type mentions: list[str]
    :param user_hd_profile_pic_url: High-resolution profile picture URL extracted from item author.
    :type user_hd_profile_pic_url: str
    """

    id: str
    code: str
    taken_at: datetime
    owner_username: str
    caption: str
    media_type: str
    resources: list[MediaResource]
    like_count: int = 0
    comment_count: int = 0
    play_count: int = 0
    expiring_at: datetime | None = None
    location: LocationInfo | None = None
    music: MusicInfo | None = None
    hashtags: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    user_hd_profile_pic_url: str = ""

    @property
    def filename_prefix(self) -> str:
        """
        Standard formatted prefix for saved media files: ``YYYYMMDD_CODE``.

        :return: Standardized filename prefix.
        :rtype: str
        """
        date_str = self.taken_at.strftime("%Y%m%d")
        identifier = self.code or self.id
        return f"{date_str}_{identifier}"

    @property
    def local_filenames(self) -> list[str]:
        """
        List of expected filenames for all downloadable resources.

        :return: List of file names with extensions.
        :rtype: list[str]
        """
        prefix = self.filename_prefix
        if len(self.resources) <= 1:
            ext = self.resources[0].extension if self.resources else "jpg"
            return [f"{prefix}.{ext}"]
        return [f"{prefix}{res.suffix}.{res.extension}" for res in self.resources]

    @classmethod
    def from_api_dict(cls, data: dict[str, Any], content_type: str = "posts") -> MediaItem:
        """
        Parse raw Instagram API dictionaries into a strongly typed :class:`MediaItem`.

        Handles payload variants from REST mobile feeds, web profile endpoints,
        and GraphQL nodes transparently.

        :param data: Raw JSON dictionary from Instagram API.
        :type data: dict[str, Any]
        :param content_type: Context type ('posts', 'reels', 'stories', 'highlights', 'tagged').
        :type content_type: str
        :return: Fully populated domain object.
        :rtype: MediaItem
        """
        media_id = str(data.get("pk") or data.get("id") or "").replace("POLARIS_", "")
        code = str(data.get("code") or data.get("shortcode") or media_id)

        raw_ts = data.get("taken_at") or data.get("taken_at_timestamp")
        try:
            taken_at = datetime.fromtimestamp(float(raw_ts), tz=timezone.utc) if raw_ts else datetime.now(timezone.utc)
        except (ValueError, TypeError, OSError):
            taken_at = datetime.now(timezone.utc)

        raw_exp = data.get("expiring_at") or data.get("expiring_at_timestamp")
        try:
            expiring_at = datetime.fromtimestamp(float(raw_exp), tz=timezone.utc) if raw_exp else None
        except (ValueError, TypeError, OSError):
            expiring_at = None

        user = data.get("user") or data.get("owner") or {}
        owner_username = str(user.get("username", "unknown") if isinstance(user, dict) else "unknown")
        hd_pic = extract_hd_profile_pic_url(data)

        caption = ""
        raw_caption = data.get("caption")
        if isinstance(raw_caption, dict):
            caption = str(raw_caption.get("text", ""))
        elif isinstance(raw_caption, str):
            caption = raw_caption
        else:
            edges = (data.get("edge_media_to_caption") or {}).get("edges", [])
            if edges and isinstance(edges, list) and isinstance(edges[0], dict):
                caption = str((edges[0].get("node") or {}).get("text", ""))

        resources: list[MediaResource] = []
        raw_mt = data.get("media_type")
        is_carousel = raw_mt == 8 or bool(data.get("carousel_media") or data.get("edge_sidecar_to_children"))

        if is_carousel:
            media_type = "album"
            children = data.get("carousel_media") or [
                e.get("node") for e in (data.get("edge_sidecar_to_children") or {}).get("edges", [])
            ]
            for index, child in enumerate(children):
                if not isinstance(child, dict):
                    continue
                child_is_video = child.get("media_type") == 2 or bool(child.get("is_video"))
                child_res = cls._extract_best_resource(child, is_video=child_is_video, suffix=f"_{index + 1}")
                if child_res:
                    resources.append(child_res)
        else:
            is_video = (
                raw_mt == 2
                or bool(data.get("is_video"))
                or data.get("product_type") == "clips"
                or data.get("__typename") == "XIGPolarisVideoMedia"
            )
            media_type = "video" if is_video else "photo"
            single_res = cls._extract_best_resource(data, is_video=is_video)
            if single_res:
                resources.append(single_res)

        like_count = int(
            data.get("like_count")
            or (data.get("edge_media_preview_like") or {}).get("count")
            or 0
        )
        comment_count = int(
            data.get("comment_count")
            or (data.get("edge_media_to_parent_comment") or {}).get("count")
            or (data.get("edge_media_to_comment") or {}).get("count")
            or 0
        )
        play_count = int(
            data.get("play_count")
            or data.get("view_count")
            or data.get("ig_play_count")
            or 0
        )

        hashtags = list(set(_HASHTAG_RE.findall(caption)))
        mentions = cls._parse_mentions(caption, data)
        location = cls._parse_location(data)
        music = cls._parse_music(data)

        return cls(
            id=media_id,
            code=code,
            taken_at=taken_at,
            owner_username=owner_username,
            caption=caption,
            media_type=media_type,
            resources=resources,
            like_count=like_count,
            comment_count=comment_count,
            play_count=play_count,
            expiring_at=expiring_at,
            location=location,
            music=music,
            hashtags=hashtags,
            mentions=mentions,
            user_hd_profile_pic_url=hd_pic,
        )

    @staticmethod
    def _extract_best_resource(data: dict[str, Any], is_video: bool, suffix: str = "") -> MediaResource | None:
        """
        Extract highest quality image or video resource descriptor from raw node data.

        :param data: Resource JSON mapping.
        :type data: dict[str, Any]
        :param is_video: Whether the target resource is a video.
        :type is_video: bool
        :param suffix: Optional filename suffix for multi-resource album children.
        :type suffix: str
        :return: Extracted MediaResource instance, or None if unavailable.
        :rtype: MediaResource | None
        """
        if is_video:
            versions = data.get("video_versions") or []
            if versions and isinstance(versions, list) and isinstance(versions[0], dict):
                best = versions[0]
                return MediaResource(
                    url=str(best.get("url", "")),
                    is_video=True,
                    width=int(best.get("width", 0)),
                    height=int(best.get("height", 0)),
                    suffix=suffix,
                )
            if direct_url := data.get("video_url"):
                return MediaResource(url=str(direct_url), is_video=True, suffix=suffix)

        candidates = (data.get("image_versions2") or {}).get("candidates") or []
        if candidates and isinstance(candidates, list) and isinstance(candidates[0], dict):
            best = candidates[0]
            return MediaResource(
                url=str(best.get("url", "")),
                is_video=False,
                width=int(best.get("width", 0)),
                height=int(best.get("height", 0)),
                suffix=suffix,
            )
        if display_url := data.get("display_url"):
            return MediaResource(url=str(display_url), is_video=False, suffix=suffix)

        return None

    @staticmethod
    def _parse_mentions(caption: str, data: dict[str, Any]) -> list[str]:
        """
        Extract mentioned and tagged user handles from captions and media metadata.

        :param caption: Caption text string.
        :type caption: str
        :param data: Raw JSON media dictionary.
        :type data: dict[str, Any]
        :return: Deduplicated list of mentioned usernames.
        :rtype: list[str]
        """
        mentions = set(_MENTION_RE.findall(caption))

        tags = (data.get("usertags") or {}).get("in", [])
        if not tags:
            edges = (data.get("edge_media_to_tagged_user") or {}).get("edges", [])
            tags = [e.get("node") or {} for e in edges if isinstance(e, dict)]

        for tag in tags:
            if isinstance(tag, dict):
                user = tag.get("user") or {}
                if isinstance(user, dict) and (username := user.get("username")):
                    mentions.add(str(username))

        for coauthor in data.get("coauthor_producers") or []:
            if isinstance(coauthor, dict) and (username := coauthor.get("username")):
                mentions.add(str(username))

        return list(mentions)

    @staticmethod
    def _parse_location(data: dict[str, Any]) -> LocationInfo | None:
        """
        Parse location identifier and place name from media metadata.

        :param data: Raw JSON media dictionary.
        :type data: dict[str, Any]
        :return: LocationInfo instance, or None if location is absent.
        :rtype: LocationInfo | None
        """
        loc = data.get("location")
        if isinstance(loc, dict) and loc.get("name"):
            return LocationInfo(id=str(loc.get("pk") or loc.get("id", "")), name=str(loc["name"]))

        locations = data.get("locations") or []
        if locations and isinstance(locations, list) and isinstance(locations[0], dict):
            first = locations[0]
            if first.get("name"):
                return LocationInfo(id=str(first.get("pk") or first.get("id", "")), name=str(first["name"]))

        return None

    @staticmethod
    def _parse_music(data: dict[str, Any]) -> MusicInfo | None:
        """
        Parse track title and artist from clips or audio metadata envelopes.

        :param data: Raw JSON media dictionary.
        :type data: dict[str, Any]
        :return: MusicInfo instance, or None if music metadata is absent.
        :rtype: MusicInfo | None
        """
        music_meta = data.get("music_metadata") or {}
        asset_info = (music_meta.get("music_info") or {}).get("music_asset_info")
        if isinstance(asset_info, dict) and asset_info.get("title"):
            return MusicInfo(title=str(asset_info.get("title", "")), artist=str(asset_info.get("display_artist", "")))

        clips_meta = data.get("clips_metadata") or {}
        clips_asset = (clips_meta.get("music_info") or {}).get("music_asset_info")
        if isinstance(clips_asset, dict) and clips_asset.get("title"):
            return MusicInfo(title=str(clips_asset.get("title", "")), artist=str(clips_asset.get("display_artist", "")))

        sound = clips_meta.get("original_sound_info") or {}
        if isinstance(sound, dict) and sound.get("original_audio_title"):
            artist = (sound.get("ig_artist") or {}).get("username") or str(sound.get("original_audio_title", ""))
            return MusicInfo(title=str(sound.get("original_audio_title", "Original Audio")), artist=str(artist))

        return None

    def to_metadata_dict(self) -> dict[str, Any]:
        """
        Serialize domain model to clean, structured JSON-serializable dictionary.

        :return: JSON-compatible metadata mapping.
        :rtype: dict[str, Any]
        """
        return {
            "id": self.id,
            "code": self.code,
            "taken_at": self.taken_at.isoformat(),
            "owner": self.owner_username,
            "type": self.media_type,
            "local_files": self.local_filenames,
            "caption": self.caption,
            "hashtags": self.hashtags,
            "mentions": self.mentions,
            "expiring_at": self.expiring_at.isoformat() if self.expiring_at else None,
            "location": {"id": self.location.id, "name": self.location.name} if self.location else None,
            "music": {"title": self.music.title, "artist": self.music.artist} if self.music else None,
            "metrics": {
                "likes": self.like_count,
                "comments": self.comment_count,
                "plays": self.play_count,
            },
            "url": f"https://www.instagram.com/p/{self.code}/" if self.code else "",
        }


@dataclass(slots=True, frozen=True)
class HighlightGroup:
    """
    Represents a collection of stories grouped under a Highlight reel.

    :param id: Highlight identifier (numeric string without prefix).
    :type id: str
    :param title: User-assigned title of the highlight.
    :type title: str
    :param cover_url: URL to the cover image.
    :type cover_url: str
    :param items: List of individual story items contained in the highlight.
    :type items: list[MediaItem]
    """

    id: str
    title: str
    cover_url: str = ""
    items: list[MediaItem] = field(default_factory=list)

    @property
    def slug(self) -> str:
        """
        Sanitized folder name for saving highlight items.

        :return: Safe directory slug.
        :rtype: str
        """
        return slugify(self.title) or f"highlight_{self.id}"


@dataclass(slots=True, frozen=True)
class Profile:
    """
    Represents an Instagram user profile.

    :param id: Numeric user ID.
    :type id: str
    :param username: Instagram handle.
    :type username: str
    :param full_name: Display name.
    :type full_name: str
    :param biography: Profile biography / description text.
    :type biography: str
    :param is_private: True if the account is private.
    :type is_private: bool
    :param is_verified: True if verified badge is present.
    :type is_verified: bool
    :param profile_pic_url: Profile picture URL.
    :type profile_pic_url: str
    :param posts_count: Total published posts count.
    :type posts_count: int
    :param followers_count: Total followers count.
    :type followers_count: int
    :param following_count: Total following count.
    :type following_count: int
    """

    id: str
    username: str
    full_name: str
    biography: str
    is_private: bool
    is_verified: bool
    profile_pic_url: str
    posts_count: int = 0
    followers_count: int = 0
    following_count: int = 0

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> Profile:
        """
        Parse profile data from web profile or REST API payloads.

        :param data: JSON mapping containing user details.
        :type data: dict[str, Any]
        :return: Profile instance.
        :rtype: Profile
        """
        user = data.get("user") or data
        user_id = str(user.get("id") or user.get("pk") or "")

        edge_media = user.get("edge_owner_to_timeline_media") or {}
        edge_followed = user.get("edge_followed_by") or {}
        edge_follow = user.get("edge_follow") or {}

        posts_count = int(user.get("media_count") or edge_media.get("count") or 0)
        followers_count = int(user.get("follower_count") or edge_followed.get("count") or 0)
        following_count = int(user.get("following_count") or edge_follow.get("count") or 0)

        return cls(
            id=user_id,
            username=str(user.get("username", "")),
            full_name=str(user.get("full_name", "")),
            biography=str(user.get("biography", "")),
            is_private=bool(user.get("is_private", False)),
            is_verified=bool(user.get("is_verified", False)),
            profile_pic_url=extract_hd_profile_pic_url(user),
            posts_count=posts_count,
            followers_count=followers_count,
            following_count=following_count,
        )