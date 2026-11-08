"""Selected Studio elements retain their identity through Agent prompt construction."""
from __future__ import annotations

import json

import pytest

import web.app as web


@pytest.mark.parametrize("field", ["id", "detail"])
def test_chat_message_preserves_same_label_layers_and_complete_metadata(field: str) -> None:
    references = []
    for location, x in [("film.html:12", 10), ("film.html:13", 80)]:
        selection = {
            "context": {"session": "chat-a", "persona": "", "project": "own-film"},
            "reference": {
                "kind": "layer",
                "clipId": "shot-1",
                "text": "标题",
                "source": "<h1>" + "完整源内容" * 160 + "</h1>",
                "path": "outputs/own-film/film.html",
                "loc": location,
                "time": 2.3,
                "box": {"x": x, "y": 20, "width": 50, "height": 30},
            },
        }
        detail = json.dumps(selection, ensure_ascii=False, sort_keys=True)
        references.append(web.ReferenceRef(
            kind="openfilm-scene", id=f"openfilm:{detail}", label="标题", detail=detail,
        ))
    request = web.ChatRequest(message="调整这两个标题", sessionId="chat-a", references=references)

    prompt = web._chat_message(request)

    for reference in references:
        expected = reference.id if field == "id" else reference.detail
        assert json.dumps(expected, ensure_ascii=False) in prompt
