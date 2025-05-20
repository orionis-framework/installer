import requests

class OrionisFrameworkApi:
    """
    OrionisFrameworkApi provides an interface to fetch and access metadata about the Orionis package from PyPI.
    This class initializes by retrieving package information from the PyPI JSON API for the 'orionis' package.
    It exposes various methods to access metadata such as the package name, version, author, description, license,
    classifiers, required Python version, keywords, and project URLs.
    Attributes:
        _baseUrl (str): The URL to the PyPI JSON API for the Orionis package.
        _info (dict): A dictionary containing the package metadata fetched from PyPI.
    Methods:
        getAllData():
            Fetches and updates the internal information dictionary with the latest data from PyPI.
        getName():
            Returns the CLI name by appending '-cli' to the package name.
        getVersion():
            Returns the version string of the framework.
        getAuthor():
            Returns the author's name.
        getAuthorEmail():
            Returns the author's email address.
        getDescription():
            Returns the summary description of the framework.
        getUrl():
            Returns the homepage URL of the project.
        getLongDescription():
        getDescriptionContentType():
            Returns the content type of the long description.
        getLicense():
            Returns the license type, defaulting to "MIT" if not specified.
        getClassifiers():
            Returns a list of PyPI classifiers for the package.
        getPythonVersion():
            Returns the required Python version specification.
        getKeywords():
            Returns a list of keywords associated with the package.
    """

    def __init__(self):
        """
        Initializes the class by setting the base URL for the Orionis PyPI package,
        initializing the information dictionary, and retrieving all package data.
        """
        self._baseUrl = "https://pypi.org/pypi/orionis/json"
        self._info = {}
        self.getAllData()

    def getAllData(self):
        """
        Fetches all data from the base URL and updates the internal info attribute.

        Sends a GET request to the specified base URL. If the request is successful (status code 200),
        parses the JSON response and updates the `_info` attribute with the value associated with the "info" key.
        Raises an exception if the request fails.

        Raises:
            Exception: If the request to the base URL fails or returns a non-200 status code.
        """
        try:
            response = requests.get(self._baseUrl, timeout=10)
            response.raise_for_status()
            data = response.json()
            self._info = data.get("info", {})
            if not self._info:
                raise ValueError("No 'info' key found in PyPI response.")
            return self._info
        except requests.RequestException as e:
            raise Exception(
                f"Error fetching data from PyPI: {e}. "
                "Please check your internet connection or try again later."
            )
        except ValueError as ve:
            raise Exception(
                f"Invalid response structure from PyPI: {ve}"
            )

    def getName(self):
        """
        Returns the CLI name by appending '-cli' to the value of the 'name' key in the _info dictionary.

        Returns:
            str: The CLI name in the format '<name>-cli'.
        """
        return f"{self._info['name']}-cli"

    def getVersion(self):
        """
        Returns the version information of the framework.

        Returns:
            str: The version string from the framework's information dictionary.
        """
        return self._info['version']

    def getAuthor(self):
        """
        Returns the author of the framework.

        Returns:
            str: The author's name as specified in the framework information.
        """
        return self._info['author']

    def getAuthorEmail(self):
        """
        Retrieve the author's email address from the internal information dictionary.

        Returns:
            str: The email address of the author.
        """
        return self._info['author_email']

    def getDescription(self):
        """
        Returns the summary description from the internal information dictionary.

        Returns:
            str: The summary description stored in the '_info' dictionary under the 'summary' key.
        """
        return self._info['summary']

    def getUrl(self):
        """
        Retrieves the homepage URL from the project's information.

        Returns:
            str: The homepage URL specified in the project's 'project_urls' under 'Homepage'.
        """
        return self._info['project_urls']['Homepage']

    def getLongDescription(self):
        """
        Returns the long description of the framework.

        Returns:
            str: The description text from the framework's information dictionary.
        """
        return self._info['description']

    def getDescriptionContentType(self):
        """
        Returns the content type of the description from the internal information dictionary.

        Returns:
            str: The content type of the description (e.g., 'text/markdown', 'text/plain').
        """
        return self._info['description_content_type']

    def getLicense(self):
        """
        Returns the license type specified in the framework information.

        If the license information is not set, defaults to "MIT".

        Returns:
            str: The license type.
        """
        return self._info['license'] or "MIT"

    def getClassifiers(self):
        """
        Returns the list of classifiers from the internal _info dictionary.

        Returns:
            list: A list of classifier strings.
        """
        return self._info['classifiers']

    def getPythonVersion(self):
        """
        Retrieves the required Python version for the framework.

        Returns:
            str: The Python version specification required by the framework, as defined in the '_info' dictionary.
        """
        return self._info['requires_python']

    def getKeywords(self):
        """
        Retrieve the list of keywords associated with the current object.

        Returns:
            list: A list of keywords from the object's information dictionary.
        """
        return self._info['keywords']