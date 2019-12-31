"""
Handle the details of building a Swagger client with the correct version of the
SDK to talk to a specific Isilon host.
"""
from __future__ import print_function
from builtins import str

try:
    import isi_sdk_8_0
except ImportError:
    isi_sdk_8_0 = None

try:
    import isi_sdk_7_2
except ImportError:
    isi_sdk_7_2 = None

import sys


def configure(host, username, password, verify_ssl=False, use_version="detect"):
    """
    Get a version specific instance of the isi_sdk and a multi-thread/client
    safe instance of IsiApiClient that can be used to interface with the
    specified host by possibly detecting the best version of the sdk to use.
    Returns a tuple consisting of the isi_sdk interface, an instance of
    IsiApiClient, and a float value set to either 8.0 or 7.2 depending on
    which version of the SDK was chosen. The IsiApiClient instance can be used
    in conjunction with the isi_sdk to interface with the specified cluster
    cluster (i.e. isi_sdk.ProtocolsApi(isi_api_cli_inst).list_nfs_exports()).
    :param string host: The name or ip-address of the host to configure the SDK
    interface to work with.
    :param string username: The username to use for authentication with the
    specified host.
    :param string password: The password to use for authentication with the
    specified host.
    :param bool verify_ssl: Specifies whether or not the Isilon cluster's SSL
    certificate should be verified.
    :param mixed use_version: Can be either "detect" in order to detect the
    correct version of the SDK to use with the specified host. Or a float value
    of 7.2 or 8.0 can be used in order to force use of that particular version
    of the SDK.
    :returns: tuple
    """
    if isi_sdk_7_2 is None and isi_sdk_8_0 is None:
        raise RuntimeError("Isilon SDK is not installed.")

    host_url = "https://" + host + ":8080"

    if use_version is None or use_version == "detect":
        host_version = _detect_host_version(host, username, password, verify_ssl)
    else:
        host_version = use_version

    isi_sdk = None
    if host_version < 8.0 and isi_sdk_7_2 is not None:
        isi_sdk = isi_sdk_7_2
    elif host_version >= 8.0 and isi_sdk_8_0 is None:
        isi_sdk = isi_sdk_7_2
        # we detected a version 8.0 host, but have to treat it like a 7.2 host
        # because the 8.0 SDK is not installed
        host_version = 7.2
    else:
        isi_sdk = isi_sdk_8_0

    configuration = isi_sdk.Configuration()
    configuration.username = username
    configuration.password = password
    configuration.verify_ssl = verify_ssl
    configuration.host = host_url
    api_client = isi_sdk.ApiClient(configuration)

    return isi_sdk, api_client, host_version


def _detect_host_version(host, username, password, verify_ssl):
    # if 7.2 is available then use it to check the version of the cluster
    # because it will work for 7.2 or newer clusters.
    isi_sdk = isi_sdk_7_2 if isi_sdk_7_2 else isi_sdk_8_0

    configuration = isi_sdk.Configuration()
    configuration.username = username
    configuration.password = password
    configuration.verify_ssl = verify_ssl
    configuration.host = "https://" + host + ":8080"
    api_client = isi_sdk.ApiClient(configuration)

    try:
        try:
            config = isi_sdk.ClusterApi(api_client).get_cluster_config()
            host_version = (
                7.2 if config.onefs_version.release.startswith("v7.") else 8.0
            )
        except isi_sdk.rest.ApiException as api_exc:
            # if we are using isi_sdk_8_0 (because 7.2 is not installed) and the
            # cluster is a 7.2 cluster then it will return 404 for the
            # get_cluster_config call, but it should still work for stats queries,
            # so just set the version and continue on.
            if isi_sdk == isi_sdk_8_0 and api_exc.status == 404:
                host_version = 7.2
            else:
                raise api_exc
    except Exception as exc:
        raise RuntimeError(
            "Failed to get cluster config for cluster %s "
            "using SDK %s. Error: %s" % (host, isi_sdk.__name__, str(exc))
        )

    if host_version == 7.2 and isi_sdk_7_2 is None:
        print(
            "Detected version 7 host, but version 7.2 SDK "
            "is not installed, will use 8.0 SDK instead.",
            file=sys.stderr,
        )

    if host_version == 8.0 and isi_sdk_8_0 is None:
        print(
            "Detected version 8 host, but version 8.0 SDK "
            "is not installed, will use 7.2 SDK instead.",
            file=sys.stderr,
        )

    return host_version
