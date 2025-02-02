"""Kernel Provisioner Classes"""
# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
import glob
from os import getenv
from os import path
from typing import Any
from typing import Dict
from typing import List

from entrypoints import EntryPoint  # type: ignore
from entrypoints import get_group_all
from entrypoints import get_single
from entrypoints import NoSuchEntryPoint
from traitlets.config import default  # type: ignore
from traitlets.config import SingletonConfigurable
from traitlets.config import Unicode

from .provisioner_base import KernelProvisionerBase


class KernelProvisionerFactory(SingletonConfigurable):
    """
    :class:`KernelProvisionerFactory` is responsible for creating provisioner instances.

    A singleton instance, `KernelProvisionerFactory` is also used by the :class:`KernelSpecManager`
    to validate `kernel_provisioner` references found in kernel specifications to confirm their
    availability (in cases where the kernel specification references a kernel provisioner that has
    not been installed into the current Python environment).

    It's `default_provisioner_name` attribute can be used to specify the default provisioner
    to use when a kernel_spec is found to not reference a provisioner.  It's value defaults to
    `"local-provisioner"` which identifies the local provisioner implemented by
    :class:`LocalProvisioner`.
    """

    GROUP_NAME = 'jupyter_client.kernel_provisioners'
    provisioners: Dict[str, EntryPoint] = {}

    default_provisioner_name_env = "JUPYTER_DEFAULT_PROVISIONER_NAME"
    default_provisioner_name = Unicode(
        config=True,
        help="""Indicates the name of the provisioner to use when no kernel_provisioner
                                       entry is present in the kernelspec.""",
    )

    @default('default_provisioner_name')
    def default_provisioner_name_default(self):
        return getenv(self.default_provisioner_name_env, "local-provisioner")

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        for ep in KernelProvisionerFactory._get_all_provisioners():
            self.provisioners[ep.name] = ep

    def is_provisioner_available(self, kernel_spec: Any) -> bool:
        """
        Reads the associated ``kernel_spec`` to determine the provisioner and returns whether it
        exists as an entry_point (True) or not (False).  If the referenced provisioner is not
        in the current cache or cannot be loaded via entry_points, a warning message is issued
        indicating it is not available.
        """
        is_available: bool = True
        provisioner_cfg = self._get_provisioner_config(kernel_spec)
        provisioner_name = str(provisioner_cfg.get('provisioner_name'))
        if not self._check_availability(provisioner_name):
            is_available = False
            self.log.warning(
                f"Kernel '{kernel_spec.display_name}' is referencing a kernel "
                f"provisioner ('{provisioner_name}') that is not available.  "
                f"Ensure the appropriate package has been installed and retry."
            )
        return is_available

    def create_provisioner_instance(
        self, kernel_id: str, kernel_spec: Any, parent: Any
    ) -> KernelProvisionerBase:
        """
        Reads the associated ``kernel_spec`` to see if it has a `kernel_provisioner` stanza.
        If one exists, it instantiates an instance.  If a kernel provisioner is not
        specified in the kernel specification, a default provisioner stanza is fabricated
        and instantiated corresponding to the current value of `default_provisioner_name` trait.
        The instantiated instance is returned.

        If the provisioner is found to not exist (not registered via entry_points),
        `ModuleNotFoundError` is raised.
        """
        provisioner_cfg = self._get_provisioner_config(kernel_spec)
        provisioner_name = str(provisioner_cfg.get('provisioner_name'))
        if not self._check_availability(provisioner_name):
            raise ModuleNotFoundError(
                f"Kernel provisioner '{provisioner_name}' has not been registered."
            )

        self.log.debug(
            f"Instantiating kernel '{kernel_spec.display_name}' with "
            f"kernel provisioner: {provisioner_name}"
        )
        provisioner_class = self.provisioners[provisioner_name].load()
        provisioner_config = provisioner_cfg.get('config')
        return provisioner_class(
            kernel_id=kernel_id,
            kernel_spec=kernel_spec,
            parent=parent,
            **provisioner_config
        )

    def _check_availability(self, provisioner_name: str) -> bool:
        """
        Checks that the given provisioner is available.

        If the given provisioner is not in the current set of loaded provisioners an attempt
        is made to fetch the named entry point and, if successful, loads it into the cache.

        :param provisioner_name:
        :return:
        """
        is_available = True
        if provisioner_name not in self.provisioners:
            try:
                ep = self._get_provisioner(provisioner_name)
                self.provisioners[provisioner_name] = ep  # Update cache
            except NoSuchEntryPoint:
                is_available = False
        return is_available

    def _get_provisioner_config(self, kernel_spec: Any) -> Dict[str, Any]:
        """
        Return the kernel_provisioner stanza from the kernel_spec.

        Checks the kernel_spec's metadata dictionary for a kernel_provisioner entry.
        If found, it is returned, else one is created relative to the DEFAULT_PROVISIONER
        and returned.

        Parameters
        ----------
        kernel_spec : Any - this is a KernelSpec type but listed as Any to avoid circular import
            The kernel specification object from which the provisioner dictionary is derived.

        Returns
        -------
        dict
            The provisioner portion of the kernel_spec.  If one does not exist, it will contain
            the default information.  If no `config` sub-dictionary exists, an empty `config`
            dictionary will be added.
        """
        env_provisioner = kernel_spec.metadata.get('kernel_provisioner', {})
        if 'provisioner_name' in env_provisioner:  # If no provisioner_name, return default
            if (
                'config' not in env_provisioner
            ):  # if provisioner_name, but no config stanza, add one
                env_provisioner.update({"config": {}})
            return env_provisioner  # Return what we found (plus config stanza if necessary)
        return {"provisioner_name": self.default_provisioner_name, "config": {}}

    def get_provisioner_entries(self) -> Dict[str, str]:
        """
        Returns a dictionary of provisioner entries.

        The key is the provisioner name for its entry point.  The value is the colon-separated
        string of the entry point's module name and object name.
        """
        return {
            name: f"{ep.module_name}:{ep.object_name}"
            for name, ep in self.provisioners.items()
        }

    @staticmethod
    def _get_all_provisioners() -> List[EntryPoint]:
        """Wrapper around entrypoints.get_group_all() - primarily to facilitate testing."""
        return get_group_all(KernelProvisionerFactory.GROUP_NAME)

    def _get_provisioner(self, name: str) -> EntryPoint:
        """Wrapper around entrypoints.get_single() - primarily to facilitate testing."""
        try:
            ep = get_single(KernelProvisionerFactory.GROUP_NAME, name)
        except NoSuchEntryPoint:
            if name != 'local-provisioner':
                raise
            distros = glob.glob(f"{path.dirname(path.dirname(__file__))}-*")
            self.log.warning(
                f"Kernel Provisioning: The 'local-provisioner' is not found.  This is likely "
                f"due to the presence of multiple jupyter_client distributions and a previous "
                f"distribution is being used as the source for entrypoints - which does not "
                f"include 'local-provisioner'.  That distribution should be removed such that "
                f"only the version-appropriate distribution remains (version >= 7).  Until "
                f"then, a 'local-provisioner' entrypoint will be automatically constructed "
                f"and used.\nThe candidate distribution locations are: {distros}"
            )
            ep = EntryPoint(
                'local-provisioner', 'jupyter_client.provisioning', 'LocalProvisioner'
            )
        return ep
