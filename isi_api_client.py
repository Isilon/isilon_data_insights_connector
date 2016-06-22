"""
Currently Swagger Codegen uses a singleton to specify basic auth
credentials, which doesn't work for multi-thread or multi-client
scenarios where each thread or client needs to connect to a unique
cluster. So this class is a custom implementation of the
isi_sdk.ApiClient that is multi-thread/client safe.
"""
class IsiApiClient(object):
    _username = None
    _password = None

    def configure_basic_auth(self, username, password):
        self._username = username
        self._password = password
