# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""OpenSearch user helper functions.

These functions wrap around some API calls used for user management.
"""

import logging
from typing import Dict, List, Optional

from charms.opensearch.v0.constants_charm import (
    AdminUser,
    COSRole,
    COSUser,
    KibanaserverUser,
    OpenSearchUsers,
)
from charms.opensearch.v0.opensearch_exceptions import (
    OpenSearchError,
    OpenSearchHttpError,
)

logger = logging.getLogger(__name__)


# The unique Charmhub library identifier, never change it
LIBID = "f9da4353bd314b86acfdfa444a9517c9"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


USER_ENDPOINT = "/_plugins/_security/api/internalusers"
ROLE_ENDPOINT = "/_plugins/_security/api/roles"
ROLESMAPPING_ENDPOINT = "/_plugins/_security/api/rolesmapping"


class OpenSearchUserMgmtError(Exception):
    """Base exception class for OpenSearch user management errors."""


class OpenSearchUserManager:
    """User management class for OpenSearch API."""

    def __init__(self, charm):
        self.charm = charm
        self.model = charm.model
        self.unit = self.charm.unit
        self.opensearch = self.charm.opensearch

    def get_roles(self) -> Dict[str, any]:
        """Gets list of roles.

        Raises:
            OpenSearchUserMgmtError: If the request fails.
        """
        try:
            return self.opensearch.request("GET", f"{ROLE_ENDPOINT}/")
        except OpenSearchHttpError as e:
            raise OpenSearchUserMgmtError(e)

    def create_role(
        self,
        role_name: str,
        permissions: Optional[Dict[str, str]] = None,
        action_groups: Optional[Dict[str, str]] = None,
    ) -> Dict[str, any]:
        """Creates a role with the given permissions.

        This method assumes the dicts provided are valid opensearch config. If not, raises
        OpenSearchUserMgmtError.

        Args:
            role_name: name of the role
            permissions: A valid dict of existing opensearch permissions.
            action_groups: A valid dict of existing opensearch action groups.

        Raises:
            OpenSearchUserMgmtError: If the role creation request fails.

        Returns:
            HTTP response to opensearch API request.
        """
        try:
            resp = self.opensearch.request(
                "PUT",
                f"{ROLE_ENDPOINT}/{role_name}",
                payload={**(permissions or {}), **(action_groups or {})},
            )
        except OpenSearchHttpError as e:
            raise OpenSearchUserMgmtError(e)

        if resp.get("status") != "CREATED" and not (
            resp.get("status") == "OK" and "updated" in resp.get("message")
        ):
            logging.error(f"Couldn't create role: {resp}")
            raise OpenSearchUserMgmtError(f"creating role {role_name} failed")

        return resp

    def remove_role(self, role_name: str) -> Dict[str, any]:
        """Remove the given role from opensearch distribution.

        Args:
            role_name: name of the role to be removed.

        Raises:
            OpenSearchUserMgmtError: If the request fails, or if role_name is empty

        Returns:
            HTTP response to opensearch API request.
        """
        if not role_name:
            raise OpenSearchUserMgmtError(
                "role name empty - sending a DELETE request to endpoint root isn't permitted"
            )

        try:
            resp = self.opensearch.request("DELETE", f"{ROLE_ENDPOINT}/{role_name}")
        except OpenSearchHttpError as e:
            if e.response_code == 404:
                return {
                    "status": "OK",
                    "response": "role does not exist, and therefore has not been removed",
                }
            else:
                raise OpenSearchUserMgmtError(e)

        logger.debug(resp)
        if resp.get("status") != "OK":
            raise OpenSearchUserMgmtError(f"removing role {role_name} failed")

        return resp

    def get_users(self) -> Dict[str, any]:
        """Gets list of users.

        Raises:
            OpenSearchUserMgmtError: If the request fails.
        """
        try:
            return self.opensearch.request("GET", f"{USER_ENDPOINT}/")
        except OpenSearchHttpError as e:
            raise OpenSearchUserMgmtError(e)

    def create_user(
        self, user_name: str, roles: Optional[List[str]], hashed_pwd: str
    ) -> Dict[str, any]:
        """Create or update user and assign the requested roles to the user.

        Args:
            user_name: name of the user to be created.
            roles: list of roles to be applied to the user. These must already exist.
            hashed_pwd: the hashed password for the user.

        Raises:
            OpenSearchUserMgmtError: If the request fails.

        Returns:
            HTTP response to opensearch API request.
        """
        payload = {"hash": hashed_pwd}
        if roles:
            payload["opendistro_security_roles"] = roles

        try:
            resp = self.opensearch.request(
                "PUT",
                f"{USER_ENDPOINT}/{user_name}",
                payload=payload,
            )
        except OpenSearchHttpError as e:
            logger.error(f"Couldn't create user {str(e)}")
            raise OpenSearchUserMgmtError(e)

        if resp.get("status") != "CREATED" and not (
            resp.get("status") == "OK" and "updated" in resp.get("message")
        ):
            raise OpenSearchUserMgmtError(f"creating user {user_name} failed")

        return resp

    def remove_user(self, user_name: str) -> Dict[str, any]:
        """Remove the given user from opensearch distribution.

        Args:
            user_name: name of the user to be removed.

        Raises:
            OpenSearchUserMgmtError: If the request fails, or if user_name is empty

        Returns:
            HTTP response to opensearch API request.
        """
        if not user_name:
            raise OpenSearchUserMgmtError(
                "user name empty - sending a DELETE request to endpoint root isn't permitted"
            )

        try:
            resp = self.opensearch.request("DELETE", f"{USER_ENDPOINT}/{user_name}")
        except OpenSearchHttpError as e:
            if e.response_code == 404:
                return {
                    "status": "OK",
                    "response": "user does not exist, and therefore has not been removed",
                }
            else:
                raise OpenSearchUserMgmtError(e)

        logger.debug(resp)
        if resp.get("status") != "OK":
            raise OpenSearchUserMgmtError(f"removing user {user_name} failed")
        return resp

    def patch_user(self, user_name: str, patches: List[Dict[str, any]]) -> Dict[str, any]:
        """Applies patches to user.

        Args:
            user_name: name of the user to be created.
            patches: a list of patches to be applied to the user in question.

        Raises:
            OpenSearchUserMgmtError: If the request fails.

        Returns:
            HTTP response to opensearch API request.
        """
        try:
            resp = self.opensearch.request(
                "PATCH",
                f"{USER_ENDPOINT}/{user_name}",
                payload=patches,
            )
        except OpenSearchHttpError as e:
            raise OpenSearchUserMgmtError(e)

        if resp.get("status") != "OK":
            raise OpenSearchUserMgmtError(f"patching user {user_name} failed")

        return resp

    def create_role_mapping(self, role: str, mapped_users: List[str]) -> None:
        """Creates or replaces role mapping for selected role with all of its users mapped to it.

        Args:
            role: name of the role for users being mapped to.
            mapped_users: all the users, that should be mapped to the specified role.

        Raises:
            OpenSearchUserMgmtError: If the request fails.
        """
        try:
            resp = self.opensearch.request(
                "PUT",
                f"{ROLESMAPPING_ENDPOINT}/{role}",
                payload={"users": mapped_users, "backend_roles": [role]},
            )
        except OpenSearchHttpError as e:
            logger.error(f"Couldn't create role mapping {str(e)}")
            raise OpenSearchUserMgmtError(e)

        if resp.get("status") != "CREATED" and not (
            resp.get("status") == "OK" and "updated" in resp.get("message")
        ):
            raise OpenSearchUserMgmtError(f"creating role mapping {role} failed")

    def remove_role_mapping(self, role: str) -> None:
        """Remove the given role mapping if it exists.

        Args:
            role: name of the role mapping to be removed.

        Raises:
            OpenSearchUserMgmtError: If the request fails, or if role is empty
        """
        if not role:
            raise OpenSearchUserMgmtError(
                "role name empty - sending a DELETE request to endpoint root isn't permitted"
            )

        try:
            resp = self.opensearch.request("DELETE", f"{ROLESMAPPING_ENDPOINT}/{role}")
        except OpenSearchHttpError as e:
            if e.response_code == 404:
                resp = {
                    "status": "OK",
                    "response": "role mapping does not exist, and therefore has not been removed",
                }
            else:
                raise OpenSearchUserMgmtError(e)

        if resp.get("status") != "OK":
            raise OpenSearchUserMgmtError(f"removing role mapping {role} failed")

    def update_user_password(self, username: str, hashed_pwd: str = None):
        """Change user hashed password."""
        resp = self.opensearch.request(
            "PATCH",
            f"/_plugins/_security/api/internalusers/{username}",
            [{"op": "replace", "path": "/hash", "value": hashed_pwd}],
        )
        if resp.get("status") != "OK":
            raise OpenSearchError(f"{resp}")

    ##########################################################################
    # Dedicated functionalities
    ##########################################################################

    def put_internal_user(self, user: str, hashed_pwd: str):
        """User creation for specific system users."""
        if user not in OpenSearchUsers:
            raise OpenSearchError(f"User {user} is not an internal user.")

        if user == AdminUser:
            # reserved: False, prevents this resource from being update-protected from:
            # updates made on the dashboard or the rest api.
            # we grant the admin user all opensearch access + security_rest_api_access
            logger.debug("putting admin to internal_users.yml")
            self.opensearch.config.put(
                "opensearch-security/internal_users.yml",
                "admin",
                {
                    "hash": hashed_pwd,
                    "reserved": False,
                    "backend_roles": [AdminUser],
                    "opendistro_security_roles": [
                        "security_rest_api_access",
                        "all_access",
                    ],
                    "description": "Admin user",
                },
            )
        elif user == KibanaserverUser:
            self.opensearch.config.put(
                "opensearch-security/internal_users.yml",
                f"{KibanaserverUser}",
                {
                    "hash": hashed_pwd,
                    "reserved": False,
                    "description": "Kibanaserver user",
                },
            )
        elif user == COSUser:
            roles = [COSRole]
            self.create_user(COSUser, roles, hashed_pwd)
            self.patch_user(
                COSUser,
                [{"op": "replace", "path": "/opendistro_security_roles", "value": roles}],
            )
