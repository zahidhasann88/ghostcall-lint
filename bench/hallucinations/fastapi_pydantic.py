"""fastapi 0.115.6 / pydantic 2.10.4 facts, each verified by running the line.

``Depends`` takes ``dependency``; ``BaseModel.parse_raw`` is a pydantic 1 API that
pydantic 2 marks ``@deprecated`` (it raises ``PydanticDeprecatedSince20`` when
warnings are errors).  pydantic's top-level package resolves names lazily through
``__getattr__``, so the class is imported from ``pydantic.main``.
"""

import fastapi
from pydantic.main import BaseModel

# ghostcall-expect: GC004 13
dependency = fastapi.Depends(dependancy=None)
# ghostcall-expect: GC007 15
model = BaseModel.parse_raw("{}")
print(dependency, model)
