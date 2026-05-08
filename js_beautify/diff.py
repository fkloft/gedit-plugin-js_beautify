import dataclasses
import difflib
from typing import List, Union


@dataclasses.dataclass
class DiffBlock:
    start_a: int
    start_b: int
    end_a: int
    end_b: int
    
    removed: List[str]
    added: List[str]


@dataclasses.dataclass
class MatchBlock:
    start_a: int
    start_b: int
    size: int
    
    lines: List[str]


Diff = List[Union[DiffBlock, MatchBlock]]


def generate_diff(lines_a: List[str], lines_b: List[str]) -> Diff:
    matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
    matches = matcher.get_matching_blocks()
    
    last = matches.pop(0)
    result: Diff = []
    
    if last.a or last.b:
        result.append(DiffBlock(
            0, 0, last.a, last.b,
            lines_a[0:last.a], lines_b[0:last.b],
        ))
    if last.size:
        result.append(MatchBlock(
            last.a, last.b, last.size,
            lines_a[last.a:last.a + last.size],
        ))
    
    for match in matches:
        start_a = last.a + last.size
        end_a = match.a
        start_b = last.b + last.size
        end_b = match.b
        
        a = lines_a[start_a:end_a]
        b = lines_b[start_b:end_b]
        
        if a or b:
            result.append(DiffBlock(start_a, start_b, end_a, end_b, a, b))
        
        last = match
        
        if last.size:
            result.append(MatchBlock(
                last.a, last.b, last.size,
                lines_a[last.a:last.a + last.size],
            ))
    
    final_a = lines_a[last.a:]
    final_b = lines_b[last.b:]
    if final_a or final_b:
        result.append(DiffBlock(
            last.a, last.b, last.a + len(final_a), last.b + len(final_b),
            final_a, final_b,
        ))
    
    return result
