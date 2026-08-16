from cerebral.commands import Command, CommandRegistry


async def dummy_handler():
    return "ok"


def test_register_and_match_exact_phrase() -> None:
    reg = CommandRegistry()
    cmd = Command(name="thing", phrases=("do the thing",), handler=dummy_handler)
    reg.register(cmd)
    assert reg.match("do the thing") is cmd
    assert reg.match("Do The Thing") is cmd
    assert reg.match("do the thing please") is None


def test_match_slash_syntax() -> None:
    reg = CommandRegistry()
    cmd = Command(name="foo", phrases=("foo bar",), handler=dummy_handler)
    reg.register(cmd)
    assert reg.match("/foo") is cmd
    assert reg.match("/foo extra args") is cmd
    assert reg.match("/unknown") is None


def test_no_match_returns_none() -> None:
    reg = CommandRegistry()
    cmd = Command(name="x", phrases=("x",), handler=dummy_handler)
    reg.register(cmd)
    assert reg.match("y") is None
    assert CommandRegistry().match("y") is None


def test_re_register_same_name_replaces() -> None:
    reg = CommandRegistry()
    cmd1 = Command(name="x", phrases=("phrase1",), handler=dummy_handler)
    cmd2 = Command(name="x", phrases=("phrase2",), handler=dummy_handler)
    reg.register(cmd1)
    assert reg.match("phrase1") is cmd1
    reg.register(cmd2)
    assert reg.match("phrase2") is cmd2
    assert reg.match("phrase1") is None
    assert reg.match("x") is cmd2


def test_commands_lists_registered() -> None:
    reg = CommandRegistry()
    c1 = Command(name="a", phrases=("a",), handler=dummy_handler)
    c2 = Command(name="b", phrases=("b",), handler=dummy_handler)
    reg.register(c1)
    reg.register(c2)
    cmds = reg.commands()
    assert len(cmds) == 2
    assert {c.name for c in cmds} == {"a", "b"}
