"""No loop of the agent hands an interrupt past the handlers written around it.

CPython 3.12 acts on a pending signal at a loop's backward jump only once it
has jumped, and looks up the handler for what the signal raises at the
instruction before the jump's target (python/cpython#108214). When the loop
opens a ``try`` block, that instruction is outside the block: a Ctrl+C taken
on a ``continue`` goes past ``except KeyboardInterrupt`` and ``finally`` alike.
The remedy is the loop in a function of its own, a frame below its handlers,
as ``FileUploader._poll`` and ``wizard._await_approval`` are.

This reads the compiled code of every module of the agent and holds that no
jump is of that kind. ``test_agent.py`` has the one such interrupt that can be
placed for real, on the upload loop.
"""

import dis
import sys
from pathlib import Path

import mascope_file_agent


PACKAGE = Path(mascope_file_agent.__file__).parent


def _codes(code):
    """``code`` and every function, class body and comprehension inside it."""
    yield code
    for const in code.co_consts:
        if hasattr(const, "co_code"):
            yield from _codes(const)


def _reached(entries, offset):
    """The handlers an exception raised at ``offset`` goes through, innermost first."""
    chain = []
    while True:
        handler = next((e.target for e in entries if e.start <= offset < e.end), None)
        if handler is None or handler in chain:
            return chain
        chain.append(handler)
        offset = handler


def lost_interrupts(source: str, name: str) -> list[str]:
    """Where in ``source`` an interrupt goes past a handler that covers the loop.

    For each backward jump that looks for signals: a handler reached from the
    jump's target and not from the instruction before it, which is where 3.12
    looks the handler up.
    """
    found = []
    for code in _codes(compile(source, name, "exec")):
        bytecode = dis.Bytecode(code)
        entries = bytecode.exception_entries
        instructions = list(bytecode)
        offsets = [instruction.offset for instruction in instructions]
        for instruction in instructions:
            if instruction.opname != "JUMP_BACKWARD":
                continue
            target = instruction.argval
            looked_up = offsets[offsets.index(target) - 1]
            covering = _reached(entries, target)
            if any(handler not in _reached(entries, looked_up) for handler in covering):
                found.append(f"{name}:{instruction.positions.lineno} in {code.co_name}")
    return found


def _is_the_python_this_was_worked_out_for():
    """The rule above is 3.12's, and so is the bytecode it is read from.

    3.13 looks for the signal before it jumps, and there the jump that loses
    an interrupt is another one: the loop's own, back from the end of its
    body, which that compiler leaves outside the block. 3.14 loses neither. A
    move to another Python has to work the rule out again, not skip it.
    """
    assert sys.version_info[:2] == (3, 12), (
        "this holds a rule of CPython 3.12's; see the function's docstring "
        "before running the agent on another Python"
    )


def test_the_scan_knows_a_loop_that_loses_an_interrupt():
    """The shape the upload loop had, and the one it has now."""
    _is_the_python_this_was_worked_out_for()
    around_the_loop = (
        "def wait(queue, Empty):\n"
        "    try:\n"
        "        while True:\n"
        "            try:\n"
        "                queue.get_nowait()\n"
        "            except Empty:\n"
        "                continue\n"
        "    except KeyboardInterrupt:\n"
        "        return\n"
    )
    a_frame_away = (
        "def wait(queue, Empty):\n"
        "    try:\n"
        "        poll(queue, Empty)\n"
        "    except KeyboardInterrupt:\n"
        "        return\n"
        "\n"
        "def poll(queue, Empty):\n"
        "    while True:\n"
        "        try:\n"
        "            queue.get_nowait()\n"
        "        except Empty:\n"
        "            continue\n"
    )

    assert lost_interrupts(around_the_loop, "shape.py") == ["shape.py:7 in wait"]
    assert lost_interrupts(a_frame_away, "shape.py") == []


def test_no_loop_of_the_agent_loses_an_interrupt():
    _is_the_python_this_was_worked_out_for()
    modules = sorted(PACKAGE.glob("*.py"))
    assert len(modules) > 5, f"found too little of the agent under {PACKAGE}"

    found = []
    for module in modules:
        found += lost_interrupts(module.read_text(encoding="utf-8"), module.name)

    assert found == [], (
        "an interrupt taken on these jumps is not seen by the handlers around "
        "the loop; move the loop into a function of its own"
    )
