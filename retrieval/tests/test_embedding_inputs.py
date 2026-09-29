from __future__ import annotations

from dataclasses import dataclass, replace

from retrieval.chunker import ChunkKind, CodeChunk
from retrieval.embedding_inputs import build_embedding_input, compute_embedding_key, estimate_tokens

METHOD_CHUNK = CodeChunk(
    file_path="src/flask/app.py",
    start_line=10,
    end_line=12,
    kind=ChunkKind.METHOD,
    name="dispatch_request",
    qualified_name="Flask.dispatch_request",
    parent_class="Flask",
    text="    def dispatch_request(self):\n        return self.view()",
)


@dataclass
class KeyedEmbedder:
    model_id: str = "model-a"
    dimensions: int = 768
    task_type: str = "RETRIEVAL_DOCUMENT"


def test_the_input_names_the_file_and_definition_before_the_text():
    assert build_embedding_input(METHOD_CHUNK) == (
        "# File: src/flask/app.py\n"
        "# Method: Flask.dispatch_request\n"
        "    def dispatch_request(self):\n"
        "        return self.view()"
    )


def test_a_later_part_gets_its_part_number_and_the_signature_it_lacks():
    later_part = replace(
        METHOD_CHUNK,
        text="        return self.view()",
        signature="def dispatch_request(self):",
        part_number=2,
        part_count=3,
    )

    assert build_embedding_input(later_part) == (
        "# File: src/flask/app.py\n"
        "# Method: Flask.dispatch_request (part 2 of 3)\n"
        "def dispatch_request(self):\n"
        "        return self.view()"
    )


def test_module_code_is_described_without_its_placeholder_name():
    module_chunk = replace(
        METHOD_CHUNK, kind=ChunkKind.MODULE, name="<module>", qualified_name="<module>"
    )

    assert build_embedding_input(module_chunk).splitlines()[1] == "# Module-level code"


def test_the_key_is_stable_for_the_same_model_settings_and_text():
    first_key = compute_embedding_key(KeyedEmbedder(), "text")
    second_key = compute_embedding_key(KeyedEmbedder(), "text")

    assert first_key == second_key
    assert len(first_key) == 64


def test_the_key_changes_when_the_model_dimensions_task_or_text_change():
    base_key = compute_embedding_key(KeyedEmbedder(), "text")

    changed_keys = [
        compute_embedding_key(KeyedEmbedder(model_id="model-b"), "text"),
        compute_embedding_key(KeyedEmbedder(dimensions=1536), "text"),
        compute_embedding_key(KeyedEmbedder(task_type="RETRIEVAL_QUERY"), "text"),
        compute_embedding_key(KeyedEmbedder(), "text "),
    ]

    assert base_key not in changed_keys
    assert len(set(changed_keys)) == len(changed_keys)


def test_token_estimates_are_pessimistic_for_ascii_and_other_scripts():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcdef") == 2
    assert estimate_tokens("abcdefg") == 3
    assert estimate_tokens("日本語") == 3
    assert estimate_tokens("abc日本") == 3
