from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ArtifactCandidate:
    path: Path
    status: str
    reason: str
    mime_type: str
    size_bytes: int


class ArtifactService:
    _SIMPLE_PATH_PATTERN = re.compile(r"""(/[^\s`'"]+)""")
    _QUOTED_DOUBLE_PATH_PATTERN = re.compile(r'"(/[^"\n]+)"')
    _QUOTED_SINGLE_PATH_PATTERN = re.compile(r"'(/[^'\n]+)'")
    _UNQUOTED_STOP_CHARS = "\"'`"
    _TRAILING_PUNCTUATION = ".,;:!?)"
    _MAX_UNQUOTED_SCAN = 512

    def __init__(self, config) -> None:
        self.config = config
        self._artifact_roots = [Path(root).resolve() for root in config.artifact_roots]

    def detect(self, text: str, run_started_at: float) -> List[ArtifactCandidate]:
        candidates: Dict[str, ArtifactCandidate] = {}
        for raw_path in self._extract_paths(text):
            resolved = self._resolve_candidate(raw_path)
            if resolved is None:
                continue
            stat = resolved.stat()
            status, reason = self._classify(resolved, stat.st_mtime, stat.st_size, run_started_at)
            candidates[str(resolved)] = ArtifactCandidate(
                path=resolved,
                status=status,
                reason=reason,
                mime_type="application/octet-stream",
                size_bytes=int(stat.st_size),
            )
        return list(candidates.values())

    def _extract_paths(self, text: str) -> List[str]:
        extracted: List[str] = []
        seen = set()
        for pattern in (
            self._QUOTED_DOUBLE_PATH_PATTERN,
            self._QUOTED_SINGLE_PATH_PATTERN,
        ):
            for match in pattern.findall(text):
                if match not in seen:
                    extracted.append(match)
                    seen.add(match)

        for match in self._SIMPLE_PATH_PATTERN.findall(text):
            normalized = self._trim_trailing_punctuation(match)
            if normalized and normalized not in seen:
                extracted.append(normalized)
                seen.add(normalized)

        for match in self._extract_unquoted_with_spaces(text):
            if match not in seen:
                extracted.append(match)
                seen.add(match)
        return extracted

    def _extract_unquoted_with_spaces(self, text: str) -> List[str]:
        matches: List[str] = []
        for start_index, char in enumerate(text):
            if char != "/":
                continue
            raw_segment = self._read_unquoted_segment(text, start_index)
            if not raw_segment or " " not in raw_segment:
                continue
            longest = self._longest_existing_file_prefix(raw_segment)
            if longest is not None:
                matches.append(longest)
        return matches

    def _read_unquoted_segment(self, text: str, start_index: int) -> str:
        end_index = start_index
        limit = min(len(text), start_index + self._MAX_UNQUOTED_SCAN)
        while end_index < limit:
            char = text[end_index]
            if char == "\n" or char in self._UNQUOTED_STOP_CHARS:
                break
            end_index += 1
        return text[start_index:end_index].strip()

    def _longest_existing_file_prefix(self, raw_segment: str) -> Optional[str]:
        for end_index in range(len(raw_segment), 0, -1):
            candidate = raw_segment[:end_index].rstrip()
            candidate = self._trim_trailing_punctuation(candidate)
            if not candidate:
                continue
            resolved = self._resolve_candidate(candidate)
            if resolved is not None:
                return candidate
        return None

    def _trim_trailing_punctuation(self, raw_path: str) -> str:
        candidate = raw_path.rstrip()
        while candidate and candidate[-1] in self._TRAILING_PUNCTUATION:
            candidate = candidate[:-1].rstrip()
        return candidate

    def _resolve_candidate(self, raw_path: str) -> Optional[Path]:
        try:
            resolved = Path(raw_path).resolve()
        except OSError:
            return None
        if not resolved.exists() or not resolved.is_file():
            return None
        return resolved

    def _classify(
        self, path: Path, modified_at: float, size_bytes: int, run_started_at: float
    ) -> tuple[str, str]:
        text_path = str(path)
        for marker in self.config.sensitive_path_markers:
            if marker and marker in text_path:
                return "blocked", "path contains sensitive marker"
        if not self._is_within_roots(path):
            return "blocked", "outside allowed artifact roots"
        if modified_at < run_started_at:
            return "blocked", "file predates run start"
        if size_bytes > self.config.max_artifact_bytes:
            return "blocked", "file exceeds size limit"
        return "allowed", "within allowed artifact roots"

    def _is_within_roots(self, path: Path) -> bool:
        for root in self._artifact_roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False
