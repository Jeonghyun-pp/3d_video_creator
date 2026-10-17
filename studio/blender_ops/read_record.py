"""What an author script read of its shot: the shot handed to it wrapped so every read is recorded as a JSON path, and
the comparison of those paths between the shot that made a checkpoint and the shot now. Pure (no bpy), imported on both
sides: build_author.py records (author_reads.json beside the checkpoint), frames_check.checkpoint_stale compares.

Why (review 2026-10-17): the checkpoint was compared on the shot without the camera fields frames apply rewrites, as if
no author read them - but an author may pose its subject from them (a rocket author leaned the booster by
camera.move.frames[].tilt_deg), so a changed lean kept the old checkpoint. Not a list of fields an author may read: the
reads themselves, so the next author reading another field is caught too.

A read is (kind, path): 'value' - the value at path (a leaf read, a key looked up, a container copied whole), 'keys' -
which keys a mapping has (iterated), 'len' - how long a list is. Reads the recorder cannot see (a C routine reading the
dict's storage directly, such as json.dumps) are not recorded; the fields outside the camera step are compared whole
anyway (checkpoint_stale), so only a camera field read that way would go unseen.
"""
MISSING = '<missing>'


def recorded(value, reads, path=()):
    """`value` wrapped so reads of it add to `reads` (a set of (kind, path))."""
    if isinstance(value, dict):
        return _Map(value, reads, path)
    if isinstance(value, list):
        return _List(value, reads, path)
    return value


def _plain(value):
    if isinstance(value, _Map):
        return {k: _plain(dict.__getitem__(value, k)) for k in dict.keys(value)}
    if isinstance(value, _List):
        return [_plain(v) for v in list.__iter__(value)]
    return value


class _Map(dict):
    def __init__(self, data, reads, path):
        super().__init__(data)
        self._reads, self._path = reads, tuple(path)

    def _child(self, key):
        value = dict.__getitem__(self, key)
        if not isinstance(value, (dict, list)):
            self._reads.add(('value', self._path + (key,)))
        return recorded(value, self._reads, self._path + (key,))

    def __getitem__(self, key):
        if not dict.__contains__(self, key):
            self._reads.add(('value', self._path + (key,)))
        return self._child(key)

    def get(self, key, default=None):
        if not dict.__contains__(self, key):
            self._reads.add(('value', self._path + (key,)))
            return default
        return self._child(key)

    def __contains__(self, key):
        self._reads.add(('value', self._path + (key,)) if not dict.__contains__(self, key) else ('keys', self._path))
        return dict.__contains__(self, key)

    def __iter__(self):
        self._reads.add(('keys', self._path))
        return dict.__iter__(self)

    def keys(self):
        self._reads.add(('keys', self._path))
        return dict.keys(self)

    def items(self):
        self._reads.add(('keys', self._path))
        return [(k, self._child(k)) for k in dict.keys(self)]

    def values(self):
        self._reads.add(('keys', self._path))
        return [self._child(k) for k in dict.keys(self)]

    def __len__(self):
        self._reads.add(('keys', self._path))
        return dict.__len__(self)

    def _whole(self):
        self._reads.add(('value', self._path))
        return _plain(self)

    def copy(self):
        return self._whole()

    def __deepcopy__(self, memo):
        import copy
        return copy.deepcopy(self._whole(), memo)

    def __reduce_ex__(self, protocol):
        return (dict, (self._whole(),))

    def __eq__(self, other):
        return self._whole() == _plain(other)

    __hash__ = None


class _List(list):
    def __init__(self, data, reads, path):
        super().__init__(data)
        self._reads, self._path = reads, tuple(path)

    def _child(self, index):
        value = list.__getitem__(self, index)
        if not isinstance(value, (dict, list)):
            self._reads.add(('value', self._path + (index,)))
        return recorded(value, self._reads, self._path + (index,))

    def __getitem__(self, index):
        if isinstance(index, slice):
            self._reads.add(('len', self._path))
            return [self._child(i) for i in range(*index.indices(list.__len__(self)))]
        if index < 0:
            self._reads.add(('len', self._path))
            index += list.__len__(self)
        return self._child(index)

    def __iter__(self):
        self._reads.add(('len', self._path))
        return iter([self._child(i) for i in range(list.__len__(self))])

    def __len__(self):
        self._reads.add(('len', self._path))
        return list.__len__(self)

    def _whole(self):
        self._reads.add(('value', self._path))
        return _plain(self)

    def __contains__(self, item):
        return item in self._whole()

    def copy(self):
        return self._whole()

    def __deepcopy__(self, memo):
        import copy
        return copy.deepcopy(self._whole(), memo)

    def __reduce_ex__(self, protocol):
        return (list, (self._whole(),))

    def __eq__(self, other):
        return self._whole() == _plain(other)

    __hash__ = None


def as_json(reads):
    """The reads as a sorted JSON list [[kind, [path...]], ...]."""
    return [[kind, list(path)] for kind, path in sorted(reads, key=lambda r: (r[0], [str(p) for p in r[1]]))]


def _at(data, path):
    for step in path:
        if isinstance(data, dict) and step in data:
            data = data[step]
        elif isinstance(data, list) and isinstance(step, int) and -len(data) <= step < len(data):
            data = data[step]
        else:
            return MISSING
    return data


def read_of(data, kind, path):
    """What a read saw: the value at path, the sorted keys of the mapping there, or the length of the list there."""
    found = _at(data, path)
    if kind == 'keys':
        return sorted(found) if isinstance(found, dict) else MISSING
    if kind == 'len':
        return len(found) if isinstance(found, list) else MISSING
    return found


def changed(reads, then, now):
    """The paths of `reads` (as_json rows) that read differently in `now` than in `then`, as 'a/b/0/c' strings."""
    return sorted({'/'.join(str(p) for p in path) or '(the whole shot)' for kind, path in reads
                   if read_of(then, kind, path) != read_of(now, kind, path)})
