"""v3.8 §14.16 — управление памятью словами, без платного классификатора.

Замыкает жизненный цикл: до этого «забыто» существовало в схеме, но
достижимо было только правкой базы руками.
"""

import pytest
from sqlalchemy import select

from helm_core.knowledge.admin import detect_admin_command, try_admin_command
from helm_core.knowledge.memory import _markdown_mirror_path, try_remember
from helm_core.knowledge.probe import probe
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (
    KnowledgeMemory, KnowledgeMemoryStatus, KnowledgeUser, KnowledgeUserRole,
)

CHANNEL = "telegram_knowledge"


@pytest.fixture
def vault(tmp_path):
    return str(tmp_path / "vault")


@pytest.fixture
def secondary(session):
    user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(user)
    session.flush()
    return user


def _remember(session, text, vault, user_id=None):
    outcome = try_remember(session, channel=CHANNEL, text=text,
                           knowledge_user_id=user_id, vault_root=vault)
    session.flush()
    return outcome.memory


# ── разбор команды ───────────────────────────────────────────────────────

def test_forget_is_recognised():
    assert detect_admin_command("Забудь про код домофона").kind == "forget"


def test_remember_command_is_not_mistaken_for_forget():
    """«Не забудь купить молоко» — это «запомни», а не «забудь». Якорь на
    начало строки решает это без отдельной проверки."""
    assert detect_admin_command("Не забудь купить молоко") is None


def test_purge_wins_over_forget():
    """«Удали навсегда» обязано разбираться раньше обратимых команд —
    иначе необратимое действие ушло бы в обратимую ветку."""
    command = detect_admin_command("Удали навсегда код домофона")
    assert command.kind == "purge"


def test_restore_is_recognised():
    assert detect_admin_command("Верни в память код домофона").kind == "restore"


def test_fix_splits_target_and_replacement():
    command = detect_admin_command("Исправь код домофона: теперь 9999")
    assert command.kind == "fix"
    assert command.target == "код домофона"
    assert command.replacement == "теперь 9999"


def test_fix_without_replacement_is_not_a_command():
    assert detect_admin_command("Исправь код домофона") is None


def test_ordinary_text_is_not_a_command():
    assert detect_admin_command("какой код домофона") is None
    assert try_admin_command(None, text="какой код домофона").status == "not_command"


# ── забыть и вернуть ─────────────────────────────────────────────────────

def test_forget_hides_from_recall_but_keeps_the_row(session, vault):
    memory = _remember(session, "Запомни: код домофона 4512", vault)
    assert probe(session, query="какой код домофона").outcome == "LOCAL_ANSWER"

    outcome = try_admin_command(session, text="Забудь про код домофона",
                                vault_root=vault)

    assert outcome.status == "forgotten"
    assert "4512" in outcome.text
    assert session.get(KnowledgeMemory, memory.id).status == KnowledgeMemoryStatus.DISABLED
    assert probe(session, query="какой код домофона").outcome == "NEEDS_REASONING"


def test_forget_removes_the_markdown_mirror(session, vault):
    """§14.11: зеркало исключается для забытого — иначе Obsidian и
    Graphify продолжали бы показывать то, что человек убрал."""
    memory = _remember(session, "Запомни: код домофона 4512", vault)
    owner_id = bind_knowledge_user(session, None)
    mirror = _markdown_mirror_path(vault, owner_id, memory.id)
    assert mirror.exists()

    try_admin_command(session, text="Забудь про код домофона", vault_root=vault)

    assert not mirror.exists()


def test_restore_brings_it_back(session, vault):
    memory = _remember(session, "Запомни: код домофона 4512", vault)
    try_admin_command(session, text="Забудь про код домофона", vault_root=vault)

    outcome = try_admin_command(session, text="Верни в память код домофона",
                                vault_root=vault)

    assert outcome.status == "restored"
    assert session.get(KnowledgeMemory, memory.id).status == KnowledgeMemoryStatus.ACTIVE
    assert probe(session, query="какой код домофона").outcome == "LOCAL_ANSWER"
    owner_id = bind_knowledge_user(session, None)
    assert _markdown_mirror_path(vault, owner_id, memory.id).exists()


def test_restore_looks_only_among_forgotten(session, vault):
    _remember(session, "Запомни: код домофона 4512", vault)

    outcome = try_admin_command(session, text="Верни в память код домофона",
                                vault_root=vault)

    assert outcome.status == "not_found"
    assert "забытого" in outcome.text


# ── удалить навсегда ─────────────────────────────────────────────────────

def test_purge_deletes_the_row_and_the_mirror(session, vault):
    memory = _remember(session, "Запомни: код домофона 4512", vault)
    owner_id = bind_knowledge_user(session, None)
    mirror = _markdown_mirror_path(vault, owner_id, memory.id)
    memory_id = memory.id

    outcome = try_admin_command(session, text="Удали навсегда код домофона",
                                vault_root=vault)

    assert outcome.status == "purged"
    assert "необратимо" in outcome.text
    assert session.get(KnowledgeMemory, memory_id) is None
    assert not mirror.exists()


# ── исправить ────────────────────────────────────────────────────────────

def test_fix_edits_in_place_instead_of_adding_a_second_copy(session, vault):
    """Иначе «исправь» незаметно превращалось бы в «запомни ещё раз», и в
    памяти копились бы обе версии."""
    _remember(session, "Запомни: код домофона 4512", vault)

    outcome = try_admin_command(session, text="Исправь код домофона: код домофона 9999",
                                vault_root=vault)

    assert outcome.status == "corrected"
    rows = session.scalars(select(KnowledgeMemory)).all()
    assert len(rows) == 1
    assert rows[0].canonical_text == "код домофона 9999"
    # И новый текст действительно находится, а старый — нет.
    assert "9999" in probe(session, query="какой код домофона").answer_text


def test_fix_refuses_to_create_a_duplicate(session, vault):
    _remember(session, "Запомни: код домофона 4512", vault)
    _remember(session, "Запомни: пропуск в офис 8891", vault)

    outcome = try_admin_command(session, text="Исправь пропуск в офис: код домофона 4512",
                                vault_root=vault)

    assert outcome.status == "duplicate"
    assert len(session.scalars(select(KnowledgeMemory)).all()) == 2


# ── выбор цели ───────────────────────────────────────────────────────────

def test_ambiguous_target_asks_instead_of_guessing(session, vault):
    """Половина этих действий необратима — переспросить безопаснее, чем
    угадать."""
    _remember(session, "Запомни: номер машины курьера А123ВС77", vault)
    _remember(session, "Запомни: номер машины соседа В777АА99", vault)

    outcome = try_admin_command(session, text="Забудь номер машины", vault_root=vault)

    assert outcome.status == "ambiguous"
    assert "А123ВС77" in outcome.text and "В777АА99" in outcome.text
    for memory in session.scalars(select(KnowledgeMemory)).all():
        assert memory.status == KnowledgeMemoryStatus.ACTIVE


def test_nothing_found_says_so(session, vault):
    outcome = try_admin_command(session, text="Забудь про несуществующее",
                                vault_root=vault)
    assert outcome.status == "not_found"


# ── чужое не трогается ───────────────────────────────────────────────────

def test_cannot_forget_another_users_memory(session, secondary, vault):
    owner_id = bind_knowledge_user(session, None)
    _remember(session, "Запомни: код сейфа владельца 1234", vault, user_id=owner_id)

    outcome = try_admin_command(session, text="Забудь про код сейфа",
                                knowledge_user_id=secondary.id, vault_root=vault)

    assert outcome.status == "not_found"
    bind_knowledge_user(session, owner_id)
    assert session.scalars(select(KnowledgeMemory)).all()[0].status == KnowledgeMemoryStatus.ACTIVE


def test_cannot_purge_another_users_memory(session, secondary, vault):
    owner_id = bind_knowledge_user(session, None)
    _remember(session, "Запомни: код сейфа владельца 1234", vault, user_id=owner_id)

    outcome = try_admin_command(session, text="Удали навсегда код сейфа",
                                knowledge_user_id=secondary.id, vault_root=vault)

    assert outcome.status == "not_found"
    bind_knowledge_user(session, owner_id)
    assert len(session.scalars(select(KnowledgeMemory)).all()) == 1
