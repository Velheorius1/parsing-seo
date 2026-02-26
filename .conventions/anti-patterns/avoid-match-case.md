# Anti-pattern: match/case and Python 3.10+ syntax

Python 3.9+ target — do NOT use:

```python
# BAD: match/case (3.10+)
match adapter_type:
    case "api": ...

# GOOD: if/elif
if adapter_type == "api":
    ...
elif adapter_type == "html":
    ...
```

```python
# BAD: Union syntax (3.10+)
def foo(x: int | None): ...

# GOOD: Optional from typing
from typing import Optional
def foo(x: Optional[int]): ...
```
