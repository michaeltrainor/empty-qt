# Models

Qt views (`QListView`, `QTableView`, `QTreeView`) do not hold your data. A model does. mtqt wraps Python lists of objects (typically dataclasses) or nested name lists so the list stays the system of record.

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

## Tree

`SceneGraphModel` implements `QAbstractItemModel` over a nested list-of-lists forest. Each node is a list: the first element is the name (a UID), remaining elements are child nodes. Leaves are a one-element list.

```python
from mtqt import SceneGraphModel

forest = [
    ["world",
        ["persp"],
        ["geo", ["cube"]],
    ]
]
model = SceneGraphModel(forest, parent=window)
world = model.index(0, 0)
print(model.path_at(model.index(0, 0, world)))  # /world/persp
```

Load the same shape from JSON with `SceneGraphModel.from_json(path)`. Mutations go through `append_child` / `remove_row` / `set_nodes`. `UserRole` returns the `SceneNode`. The model does not own the view.

Item delegates (custom editors and painting) are the next topic after this library’s basics; v1 does not ship a custom delegate.
