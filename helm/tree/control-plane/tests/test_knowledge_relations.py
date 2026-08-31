"""P8.5.6 слой 1 knowledge_relations (E13, решение владельца 31.08.2026).

Синтетические фикстуры здесь оправданы явно: это тест парсера, не
доказательство пользы Graphify (для которого синтетика запрещена
владельцем — только реальные multi-hop вопросы на реальном корпусе).
"""
import uuid

from helm_core.knowledge.relations import (
    extract_frontmatter_relations, extract_relations, extract_wikilinks,
    note_id_for, store_relations,
)
import hashlib

from helm_core.models import KnowledgeRelation, KnowledgeSource


def _make_source(session, knowledge_user_id, *, sha256_seed: str):
    """`KnowledgeRelation.source_id` — настоящий FK на `knowledge_sources`,
    не просто UUID-строка — тесты `store_relations()` обязаны заводить
    реальную строку source, иначе вставка падает FK-нарушением."""
    source = KnowledgeSource(
        knowledge_user_id=knowledge_user_id, domain="general",
        sha256=hashlib.sha256(sha256_seed.encode()).hexdigest(),
        raw_path=f"/tmp/{sha256_seed}.txt",
    )
    session.add(source)
    session.flush()
    return source.id


# ── extract_wikilinks() ────────────────────────────────────────────────

def test_wikilink_plain_target_becomes_relates_to():
    rels = extract_wikilinks("Смотри также [[Другая заметка]] про это.")
    assert len(rels) == 1
    assert rels[0].to_id == "Другая заметка"
    assert rels[0].relation_type == "relates_to"
    assert rels[0].evidence_type == "explicit_link"


def test_wikilink_with_alias_uses_target_not_alias():
    rels = extract_wikilinks("[[Настоящее имя|видимый текст]]")
    assert len(rels) == 1
    assert rels[0].to_id == "Настоящее имя"


def test_wikilink_dedups_repeated_target_in_same_text():
    rels = extract_wikilinks("[[A]] и снова [[A]] и ещё раз [[A]]")
    assert len(rels) == 1


def test_wikilink_no_links_returns_empty():
    assert extract_wikilinks("Обычный текст без ссылок.") == []


def test_wikilink_never_invents_relation_type_beyond_relates_to():
    # Даже если текст рядом со ссылкой ЗВУЧИТ как причинность — тип связи
    # не выводится из естественного языка, только relates_to.
    rels = extract_wikilinks("Это вызвано тем, что описано в [[Причина]].")
    assert rels[0].relation_type == "relates_to"


# ── extract_frontmatter_relations() ─────────────────────────────────────

def test_frontmatter_relations_explicit_type_is_used_verbatim():
    text = (
        "---\n"
        "relations:\n"
        "  - to: \"Другая заметка\"\n"
        "    type: supports\n"
        "---\n\n"
        "Текст заметки.\n"
    )
    rels = extract_frontmatter_relations(text)
    assert len(rels) == 1
    assert rels[0].to_id == "Другая заметка"
    assert rels[0].relation_type == "supports"
    assert rels[0].evidence_type == "explicit"


def test_frontmatter_relations_multiple_entries():
    text = (
        "---\n"
        "relations:\n"
        "  - to: A\n"
        "    type: supports\n"
        "  - to: B\n"
        "    type: contradicts\n"
        "---\n"
    )
    rels = extract_frontmatter_relations(text)
    assert {(r.to_id, r.relation_type) for r in rels} == {("A", "supports"), ("B", "contradicts")}


def test_frontmatter_relation_to_can_be_a_wikilink():
    text = (
        "---\n"
        "relations:\n"
        "  - to: \"[[Третья заметка]]\"\n"
        "    type: relates_to\n"
        "---\n"
    )
    rels = extract_frontmatter_relations(text)
    assert rels[0].to_id == "Третья заметка"


def test_frontmatter_relation_without_type_is_dropped_not_guessed():
    text = (
        "---\n"
        "relations:\n"
        "  - to: \"Заметка без типа\"\n"
        "---\n"
    )
    assert extract_frontmatter_relations(text) == []


def test_frontmatter_without_relations_key_returns_empty():
    text = "---\nid: abc\ndomain: general\n---\nТекст.\n"
    assert extract_frontmatter_relations(text) == []


def test_no_frontmatter_at_all_returns_empty():
    assert extract_frontmatter_relations("Просто текст, без ---.") == []


def test_frontmatter_relations_stop_at_dedent_not_leaking_into_other_keys():
    text = (
        "---\n"
        "relations:\n"
        "  - to: A\n"
        "    type: supports\n"
        "domain: general\n"
        "---\n"
    )
    rels = extract_frontmatter_relations(text)
    assert len(rels) == 1
    assert rels[0].to_id == "A"


# ── extract_relations() комбинирует оба источника без задвоения ─────────

def test_combines_frontmatter_and_body_wikilinks():
    text = (
        "---\n"
        "relations:\n"
        "  - to: X\n"
        "    type: supports\n"
        "---\n\n"
        "См. также [[Y]].\n"
    )
    rels = extract_relations(text)
    assert {(r.to_id, r.evidence_type) for r in rels} == {("X", "explicit"), ("Y", "explicit_link")}


def test_frontmatter_wikilink_target_is_not_doubled_as_body_wikilink():
    text = (
        "---\n"
        "relations:\n"
        "  - to: \"[[Z]]\"\n"
        "    type: relates_to\n"
        "---\n\n"
        "Текст без других ссылок.\n"
    )
    rels = extract_relations(text)
    assert len(rels) == 1
    assert rels[0].evidence_type == "explicit"


# ── note_id_for() ────────────────────────────────────────────────────────

def test_note_id_uses_filename_stem():
    assert note_id_for(original_filename="Схема мышления.md", source_id=uuid.uuid4()) == "Схема мышления"


def test_note_id_falls_back_to_source_id_without_filename():
    sid = uuid.uuid4()
    assert note_id_for(original_filename=None, source_id=sid) == str(sid)


# ── store_relations() — интеграция с БД ──────────────────────────────────

def test_store_relations_writes_rows_with_full_provenance(session):
    from helm_core.knowledge.tenancy import bind_knowledge_user
    from helm_core.models import KnowledgeUser, KnowledgeUserRole

    user = KnowledgeUser(role=KnowledgeUserRole.SYSTEM_OWNER)
    session.add(user)
    session.flush()
    bind_knowledge_user(session, user.id)
    source_id = _make_source(session, user.id, sha256_seed="provenance-test")

    count = store_relations(session, knowledge_user_id=user.id, from_id="Заметка А",
                            source_id=source_id, text="Смотри [[Заметка Б]].")

    assert count == 1
    rows = session.query(KnowledgeRelation).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.knowledge_user_id == user.id
    assert row.from_id == "Заметка А"
    assert row.to_id == "Заметка Б"
    assert row.relation_type == "relates_to"
    assert row.evidence_type == "explicit_link"
    assert row.source_id == source_id
    assert row.created_at is not None


def test_store_relations_is_idempotent_on_reingest_same_source(session):
    from helm_core.knowledge.tenancy import bind_knowledge_user
    from helm_core.models import KnowledgeUser, KnowledgeUserRole

    user = KnowledgeUser(role=KnowledgeUserRole.SYSTEM_OWNER)
    session.add(user)
    session.flush()
    bind_knowledge_user(session, user.id)
    source_id = _make_source(session, user.id, sha256_seed="idempotent-test")

    store_relations(session, knowledge_user_id=user.id, from_id="A",
                    source_id=source_id, text="[[B]] [[C]]")
    store_relations(session, knowledge_user_id=user.id, from_id="A",
                    source_id=source_id, text="[[B]]")  # повторный разбор, C ушла

    rows = session.query(KnowledgeRelation).filter(KnowledgeRelation.source_id == source_id).all()
    assert {r.to_id for r in rows} == {"B"}


def test_store_relations_no_relations_in_text_writes_nothing(session):
    from helm_core.knowledge.tenancy import bind_knowledge_user
    from helm_core.models import KnowledgeUser, KnowledgeUserRole

    user = KnowledgeUser(role=KnowledgeUserRole.SYSTEM_OWNER)
    session.add(user)
    session.flush()
    bind_knowledge_user(session, user.id)

    count = store_relations(session, knowledge_user_id=user.id, from_id="A",
                            source_id=uuid.uuid4(), text="Обычный текст.")
    assert count == 0
    assert session.query(KnowledgeRelation).count() == 0
