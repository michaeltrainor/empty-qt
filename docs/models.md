# Models

Qt views (`QListView`, `QTableView`) do not hold your data. A model does. mtqt wraps a Python `list` of objects (typically dataclasses) so the list stays the system of record.

## List

`ObjectListModel` implements `rowCount` and `data`. Mutations go through `append` / `remove_row` / `set_rows` so the model emits `beginInsertRows` (and friends). Views stay in sync.

```python
from dataclasses import dataclass
from mtqt import ObjectListModel


@dataclass
class Person:
    name: str


model = ObjectListModel([Person("Ada")], display_attr="name", parent=window)
model.append(Person("Lin"))
```

`UserRole` returns the Python object. The model does not own the view.

## Table

`ObjectTableModel` uses one column per attribute name. `setData` writes back onto the object so an edit in `QTableView` updates the dataclass.

```python
from mtqt import ObjectTableModel

model = ObjectTableModel(people, columns=("name", "role"), parent=window)
```

If `columns` is omitted and the first row is a dataclass, field names are inferred.

Item delegates (custom editors and painting) are the next topic after this library’s basics; v1 does not ship a custom delegate.
