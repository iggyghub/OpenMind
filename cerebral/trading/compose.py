def compose_strategies(components: list[tuple[str, str]], mode: str) -> str:
    if mode not in ("unanimous", "majority"):
        raise ValueError(f"Unknown mode: {mode}")
    
    lines = []
    for idx, (label, source) in enumerate(components):
        indented_lines = [f"    {line}" if line.strip() else line for line in source.splitlines()]
        lines.append(f"def _component_{idx}():")
        lines.extend(indented_lines)
        lines.append("    return strategy")
        lines.append("")
        
    if mode == "unanimous":
        combine_logic = """
def _combine(signals_list, mode):
    min_len = min(len(s) for s in signals_list)
    aligned = [s[-min_len:] for s in signals_list]
    result = []
    for i in range(min_len):
        vals = [s[i] for s in aligned]
        if all(v == vals[0] for v in vals):
            result.append(vals[0])
        else:
            result.append(0)
    return result
"""
    else:
        combine_logic = """
def _combine(signals_list, mode):
    min_len = min(len(s) for s in signals_list]
    aligned = [s[-min_len:] for s in signals_list]
    result = []
    for i in range(min_len):
        s = sum(s[i] for s in aligned]
        if s > 0:
            result.append(1)
        elif s < 0:
            result.append(-1)
        else:
            result.append(0)
    return result
"""
    lines.append(combine_logic.strip() + "\n")
    
    lines.append("def strategy(data) -> list:")
    for idx in range(len(components)):
        lines.append(f"    comp{idx}_sig = _component_{idx}()(data)")
    comp_vars = ", ".join(f"comp{i}_sig" for i in range(len(components)))
    lines.append(f"    return _combine([{comp_vars}], \"{mode}\")")
    
    return "\n".join(lines)
