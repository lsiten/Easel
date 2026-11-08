"""Select live Studio endpoints derived from one explicitly bound project."""
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StudioUrls:
    editor: str = ""
    film: str = ""

    @property
    def studio(self) -> str:
        return self.editor or self.film


def live_project_urls(editor: str, film: str, is_alive: Callable[[str], bool]) -> StudioUrls:
    """A live film endpoint proves its same-service editor is ready, avoiding redirect probes."""
    if film and is_alive(film):
        return StudioUrls(editor=editor, film=film)
    if editor and is_alive(editor):
        return StudioUrls(editor=editor)
    return StudioUrls()
