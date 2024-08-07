"""
Copyright (c) 2023 Proton AG

This file is part of Proton.

Proton is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Proton is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with ProtonVPN.  If not, see <https://www.gnu.org/licenses/>.
"""
from __future__ import annotations
from importlib import metadata
import os
import inspect
import threading
import warnings
from collections import namedtuple
from typing import Optional

from ..utils import Singleton

PluggableComponent = namedtuple('PluggableComponent', ['priority', 'class_name', 'cls'])
PluggableComponentName = namedtuple('PluggableComponentName', ['type_name', 'class_name'])


class Loader(metaclass=Singleton):
    """This is the loader for pluggable components. These components are identified by a type name (string)
    and a class name (also a string).

    In normal use, one will only use :meth:`get`, as follows:

    .. code-block::

        from proton.loader import Loader
        # Note the parenthesis to instanciate an object, as Loader.get() returns a class.
        my_keyring = Loader.get('keyring')()

    You can influence which component to use using the ``PROTON_LOADER_OVERRIDES`` environment variable. It's a comma separated list
    of ``type_name=class_name`` (to force ``class_name`` to be used) and ``type_name=-class_name`` (to exclude ``class_name`` from the options considered).

    To find the candidates, ``Loader`` will use entry points, that are to be defined in setup.py, as follows:

    .. code-block::

        setup(
            #[...],
            entry_points={
                "proton_loader_keyring": [
                    "json = proton.keyring.textfile:KeyringBackendJsonFiles"
                ]
            },
            #[...]
        )

    The class pointed by these entrypoints should implement the following class methods:

    * :meth:`_get_priority`: return a numeric value, larger ones have higher priority. If it's ``None``, then this class won't be considered
    * :meth:`_validate`: check if the object can indeed be used (might be expensive/annoying). If it returns ``False``, then the backend won't be considered for the rest of the session.

    If :meth:`_validate` is not defined, then it's assumed that it will always succeed.

    To display the list of valid values, you can use ``python3 -m proton.loader``.

    """

    __loader_prefix = 'proton_loader_'

    def __init__(self):
        self.__known_types = {}
        self.__name_resolution_cache = {}
        self.__lock = threading.Lock()

    def get(
        self, type_name: str,
        class_name: Optional[str] = None,
        validate_params: Optional[dict] = None
    ) -> type:
        """Get the implementation for type_name.

        :param type_name: extension type
        :type type_name: str
        :param class_name: specific implementation to get, defaults to None (use preferred one)
        :type class_name: Optional[str], optional
        :param validate_params: pass custom arguments to backends during `_validate`
            Could be used in cases when the validity of a backend is to be dynamically
            evaluated upon calling `get()`
        :type validate_params: Optional[dict], optional
        :raises RuntimeError: if no valid implementation can be found,
            or if PROTON_LOADER_OVERRIDES is invalid.
        :return: the class implementing type_name. (careful: it's a class, not an object!)
        :rtype: class
        """
        acceptable_classes = self.get_all(type_name)

        for entry in acceptable_classes:
            # If caller specified the class he wanted, then we check only that.
            if class_name is not None:
                if entry.class_name == class_name:
                    return entry.cls
                else:
                    continue

            # Invalid priority, just continue
            # (this will fail anyway because we have ordered the list in get_all,
            # but for what it costs I prefer to go through the list)
            if entry.priority is None:
                continue

            # If we have a _validate class method, try to see if the object is indeed acceptable
            if hasattr(entry.cls, '_validate'):
                if inspect.signature(entry.cls._validate).parameters.get("validate_params"):  # noqa: E501  pylint: disable=protected-access
                    entry_is_valid = entry.cls._validate(validate_params)  # noqa: E501 pylint: disable=protected-access
                else:
                    entry_is_valid = entry.cls._validate()  # pylint: disable=protected-access

                if entry_is_valid:
                    return entry.cls
            else:
                return entry.cls

        raise RuntimeError(f"Loader: couldn't find an acceptable implementation for {type_name}.")

    @property
    def type_names(self) -> list[str]:
        """
        :return: Return a list of the known type names
        :rtype: list[str]
        """
        return [x[len(self.__loader_prefix):] for x in self._proton_entry_point_groups.keys()]

    @property
    def _proton_entry_point_groups(self):
        metadata_entry_points = metadata.entry_points()
        try:
            # importlib.metadata.entry_points() uses the selectable interface in python >= 3.10
            groups = metadata_entry_points.groups
        except AttributeError:
            # importlib.metadata.entry_points() uses the dict interface in python < 3.10
            return {
                k: v
                for k, v in metadata_entry_points.items()
                if k.startswith(self.__loader_prefix)
            }

        return {
            group: metadata_entry_points.select(group=group)
            for group in groups if group.startswith(self.__loader_prefix)
        }

    def get_all(self, type_name: str) -> list[PluggableComponent]:
        """Get a list of all implementations for ``type_name``.

        :param type_name: type of implementation to query for
        :type type_name: str
        :raises RuntimeError: if ``PROTON_LOADER_OVERRIDES`` has conflicts
        :return: Implementation for type_name (this includes the ones that are disabled)
        :rtype: list[PluggableComponent]
        """

        # If we don't have already loaded the entry points, just do so
        with self.__lock:
            # We use a lock here because a known type should only be available after it has been loaded.
            if type_name not in self.__known_types:
                metadata_group_name = self._get_metadata_group_for_typename(type_name)
                entry_points = self._proton_entry_point_groups.get(metadata_group_name, ())
                self.__known_types[type_name] = {}
                for ep in entry_points:
                    if ep.name in self.__known_types[type_name]:
                        del self.__known_types[type_name]
                        raise RuntimeError("Loader error : found 2 modules with same name (that would create security issues)")
                    try:
                        self.__known_types[type_name][ep.name] = ep.load()
                    except AttributeError:
                        warnings.warn(f"Loader: couldn't load {type_name}/{ep.name}, is it installed properly?", RuntimeWarning, stacklevel=2)
                        continue
                    self.__name_resolution_cache[self.__known_types[type_name][ep.name]] = PluggableComponentName(type_name, ep.name)

        # We do this at runtime, because we want to make sure we can change it after start.
        overrides = os.environ.get('PROTON_LOADER_OVERRIDES', '')
        overrides = [x.strip() for x in overrides.split()]
        overrides = [x[len(type_name)+1:] for x in overrides if x.startswith(f'{type_name}=')]

        force_class = set([x for x in overrides if not x.startswith('-')])
        if len(force_class) == 1:
            force_class = list(force_class)[0]
            if force_class in self.__known_types[type_name]:
                acceptable_entry_points = [force_class]
        elif len(force_class) > 1:
            raise RuntimeError(f"Loader: PROTON_LOADER_OVERRIDES contains multiple force for {type_name}")
        else:
            # Load all entry_points, except those that are excluded by PROTON_LOADER_OVERRIDES
            acceptable_entry_points = []
            for k in self.__known_types[type_name].keys():
                if '-' + k not in overrides:
                    acceptable_entry_points.append(k)

        acceptable_classes = [(v._get_priority(), k, v) for k, v in self.__known_types[type_name].items() if k in acceptable_entry_points]
        acceptable_classes += [(None, k, v) for k, v in self.__known_types[type_name].items() if k not in acceptable_entry_points]
        acceptable_classes_with_prio = [PluggableComponent(priority, class_name, v) for priority, class_name, v in acceptable_classes if priority is not None]
        acceptable_classes_without_prio = [PluggableComponent(priority, class_name, v) for priority, class_name, v in acceptable_classes if priority is None]

        # Sort the entries with priority, highest first
        acceptable_classes_with_prio.sort(reverse=True)

        return acceptable_classes_with_prio + acceptable_classes_without_prio

    def get_name(self, cls: type) -> Optional[PluggableComponentName]:
        """Return the type_name and class_name corresponding to the class in parameter.

        This is useful for inverse lookups (i.e. for logs for instance)

        :return: ``Tuple (type_name, class_name)``
        :rtype: Optional[PluggableComponentName]
        """
        return self.__name_resolution_cache.get(cls, None)

    def reset(self) -> None:
        """Erase the loader cache. (useful for tests)"""
        self.__known_types = {}
        self.__name_resolution_cache = {}

    def set_all(self, type_name: str, implementations: dict[str, type]):
        """Set a defined set of implementation for a given ``type_name``.

        This method is probably useful only for testing.

        :param type_name: Type
        :type type_name: str
        :param implementations: Dictionary implementation name -> implementation class
        :type implementations: dict[str, class]
        """
        self.__known_types[type_name] = implementations
        for class_name, cls in implementations.items():
            self.__name_resolution_cache[cls] = PluggableComponentName(type_name, class_name)

    def _get_metadata_group_for_typename(self, type_name: str) -> str:
        """Return the metadata group name for type_name

        :param type_name: type_name
        :type type_name: str
        :return: metadata group name
        :rtype: str
        """
        return self.__loader_prefix + type_name
