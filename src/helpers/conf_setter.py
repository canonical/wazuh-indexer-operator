#!/usr/bin/python
from collections.abc import Mapping
from enum import Enum

from ruamel.yaml import YAML, CommentedSeq

"""Utilities for editing yaml configuration files at any level of nestedness."""


class OutputType(Enum):
    file = 'file'
    obj = 'obj'

    def __str__(self):
        return self.value


class ConfigSetter:

    def __init__(self):
        self.yaml = YAML()

    def load(self, target_file: str):
        with open(target_file, mode='r') as f:
            data = self.yaml.load(f)

        return data

    def put(self, target_file: str, key_path: str, val: any, sep='/',
            output_type: OutputType = OutputType.file, inline_array: bool = False) -> dict[str, any]:

        with open(target_file, mode='r') as f:
            data = self.yaml.load(f)

        self.__deep_update(data, key_path.split(sep), val)

        if inline_array:
            data = self.__inline_array_format(data, key_path.split(sep), val)

        if output_type == OutputType.file:
            with open(target_file, mode='w') as f:
                self.yaml.dump(data, f)

        return data

    def delete(self, target_file: str, key_path: str, sep='/',
               output_type: OutputType = OutputType.file) -> dict[str, any]:

        with open(target_file, mode='r') as f:
            data = self.yaml.load(f)

        self.__deep_delete(data, key_path.split(sep))

        if output_type == OutputType.file:
            with open(target_file, mode='w') as f:
                self.yaml.dump(data, f)

        return data

    def __deep_update(self, source, node_keys: list[str], val: any):
        if not node_keys:
            return val

        if source is None:
            if node_keys[0].startswith('['):
                source = []
            else:
                source = {}

        current_key: str = node_keys.pop(0)

        # handling the insert / update on json objects
        if isinstance(source, Mapping):
            current_val = source.get(current_key, None)
            source[current_key] = self.__deep_update(current_val, node_keys, val)
            return source

        # handling the insert / update on arrays
        if isinstance(source, list):
            target_index = self.__target_array_index(source, current_key)
            if target_index == -1:
                source.append(self.__deep_update(None, node_keys, val))
            else:
                source[target_index] = self.__deep_update(source[target_index], node_keys, val)

            return source

        return source

    def __deep_delete(self, source, node_keys: list[str]):
        if not node_keys:
            return

        if source is None:
            return

        leaf_level = self.__leaf_level(source, node_keys)
        leaf_key = node_keys.pop(0)

        if leaf_key in leaf_level and (isinstance(leaf_level[leaf_key], Mapping) or not leaf_key.startswith('[')):
            del leaf_level[leaf_key]
            return

        # list
        target_index = self.__target_array_index(leaf_level, leaf_key)
        del leaf_level[target_index]

    def __leaf_level(self, current, node_names: list[str]):
        if len(node_names) == 1:
            return current

        current_key = node_names.pop(0)
        if current_key.startswith('[') and current_key.endswith(']'):
            target_index = self.__target_array_index(current, current_key)
            return self.__leaf_level(current[target_index], node_names)

        return self.__leaf_level(current[current_key], node_names)

    @staticmethod
    def __target_array_index(source: list, node_key: str) -> int:
        str_index = node_key[1:-1].strip()

        index = None

        # where user provides a key [key:val] or [key]
        if str_index and not str_index.isnumeric():
            key = str_index.split(':')
            if len(key) == 1:
                index = source.index(str_index)
            else:
                index = -1
                for elt, i in zip(source, range(len(source))):
                    if elt.get(key[0], None) == key[1]:
                        index = i
                        break

                if index == -1:
                    raise ValueError(f'{str_index} not found in the object.')

        exists = index is not None or (str_index and (int(str_index) < len(source)))
        if not exists:
            return -1

        return int(str_index) if index is None else index

    def __inline_array_format(self, data, node_keys: list[str], val: list[any]) -> dict[str, any]:
        leaf_k = node_keys[-1]

        leaf_l = self.__leaf_level(data, node_keys)
        leaf_l[leaf_k] = self.__flow_style()
        leaf_l[leaf_k].extend(val)

        return data

    @staticmethod
    def __flow_style() -> CommentedSeq:
        ret = CommentedSeq()
        ret.fa.set_flow_style()
        return ret
