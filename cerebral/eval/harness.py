from dataclasses import dataclass, field
from cerebral.llm.router import ToolCall, ModelRouter

@dataclass
class Case:
    name: str
    prompt: str
    expected: dict

@dataclass
class Result:
    name: str
    passed: bool
    detail: str = field(default="")

async def run_cases(router, cases: list[Case]) -> list[Result]:
    results = []
    for case in cases:
        try:
            expected = case.expected
            if "response" in expected:
                task_type = expected.get("task_type", "chat")
                actual = await router.complete(case.prompt, task_type=task_type)
                if actual == expected["response"]:
                    results.append(Result(name=case.name, passed=True, detail=""))
                else:
                    results.append(Result(
                        name=case.name, passed=False,
                        detail=f'expected response {expected["response"]!r}, got {actual!r}'
                    ))
            elif "tool" in expected:
                tools = expected.get("tools", [])
                actual = await router.complete_with_tools(case.prompt, tools)
                if not isinstance(actual, ToolCall):
                    results.append(Result(
                        name=case.name, passed=False,
                        detail=f'expected tool {expected["tool"]!r}, got {actual!r}'
                    ))
                elif actual.name != expected["tool"]:
                    results.append(Result(
                        name=case.name, passed=False,
                        detail=f'expected tool {expected["tool"]!r}, got {actual!r}'
                    ))
                elif "tool_args" in expected and actual.args != expected["tool_args"]:
                    results.append(Result(
                        name=case.name, passed=False,
                        detail=f'expected tool args {expected["tool_args"]!r}, got {actual.args!r}'
                    ))
                else:
                    results.append(Result(name=case.name, passed=True, detail=""))
            else:
                results.append(Result(name=case.name, passed=False, detail="unknown expected key"))
        except Exception as exc:
            results.append(Result(name=case.name, passed=False, detail=f"raised {exc!r}"))
    return results
