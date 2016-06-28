"""
Handle the details of building a Swagger client with the correct version of the
SDK to talk to a specific Isilon host.
"""
try:
    import isi_sdk_8_0
    from isi_api_client_8_0 import IsiApiClient_8_0
except ImportError:
    isi_sdk_8_0 = None

try:
    import isi_sdk_7_2
    from isi_api_client_7_2 import IsiApiClient_7_2
except ImportError:
    isi_sdk_7_2 = None

import sys


def configure(
        host,
        username,
        password,
        verify_ssl=False,
        use_version="detect"):
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
    host_url = "https://" + host + ":8080"

    if use_version is None or use_version is "detect":
        use_version = \
                _detect_host_version(host_url, username, password, verify_ssl)

    isi_sdk = None
    api_client_class = None
    if use_version < 8.0:
        if isi_sdk_7_2 is None:
            raise RuntimeError("Needed version (7.2) of the Isilon SDK is not" \
                    "installed.")
        isi_sdk = isi_sdk_7_2
        api_client_class = IsiApiClient_7_2
    else:
        if isi_sdk_8_0 is None:
            raise RuntimeError("Needed version (8.0) of the Isilon SDK is not" \
                    "installed.")
        isi_sdk = isi_sdk_8_0
        api_client_class = IsiApiClient_8_0


    api_client = api_client_class(host_url, verify_ssl)
    api_client.configure_basic_auth(username, password)

    return isi_sdk, api_client, use_version


def _detect_host_version(host, username, password, verify_ssl):
    # if 7.2 is available then use it to check the version of the cluster
    # because it will work for 7.2 or newer clusters.
    isi_sdk, api_client_class = (isi_sdk_7_2, IsiApiClient_7_2) \
            if isi_sdk_7_2 else ((isi_sdk_8_0, IsiApiClient_8_0) \
                if isi_sdk_8_0 else (None, None))

    if isi_sdk is None:
        raise RuntimeError("The Isilon SDK is not installed.")

    api_client = api_client_class(host, verify_ssl)
    api_client.configure_basic_auth(username, password)

    try:
        config = isi_sdk.ClusterApi(api_client).get_cluster_config()
    except isi_sdk.rest.ApiException as exc:
        raise RuntimeError("Failed to get cluster config: %s" % str(exc))

    host_version = 7.2 if config.onefs_version.release.startswith("v7.") \
            else 8.0

    if host_version == 7.2:
        if isi_sdk_7_2:
            return 7.2
        print >> sys.stderr, "Detected version 7 host, but version 7.2 SDK" \
                "is not installed, will use 8.0 instead."

    if isi_sdk_8_0:
        return 8.0

    if host_version == 8.0:
        print >> sys.stderr, "Detected version 8 host, but version 8.0 SDK" \
                "is not installed, will use 7.2 instead."

    return 7.2
