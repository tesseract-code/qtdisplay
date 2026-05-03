"""
Secure, thread-safe Cython module loader with lazy loading support.

This module provides a safe interface for loading Cython extensions with:
- Whitelist-based security validation
- Thread-safe lazy loading (PEP 562)
- Graceful fallback when extensions unavailable
- Clear error reporting with tracking IDs
"""

import importlib
import logging
import re
import sys
import threading
import uuid
from types import ModuleType
from typing import FrozenSet, Optional, List, Callable

logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

# Module configuration
MODULE_NAME = 'qtdisplay.chart.model.data._backend.points_vector'
ALLOWED_PACKAGES: FrozenSet[str] = frozenset({'qtdisplay'})

# Cython module exports
CYTHON_EXPORTS = [
    'PointsVector',
    'DualPointVector',
    'create_qpointf',
    'SeriesCache',
    'NearestPointFinder',
    'PointCacheManager',
    'adaptive_threshold',
    'downsample_to_qpointf',
]

# Always-available exports
BASE_EXPORTS = [
    'is_cython_available',
    'CythonModuleUnavailable',
    'ModuleSecurityError',
    'SecureCythonLoader',
]

# Build instructions
BUILD_INSTRUCTIONS = (
    "Build Cython extensions with: "
    "python setup.py build_ext --inplace"
)


# ============================================================================
# Exceptions
# ============================================================================

class CythonModuleUnavailable(ImportError):
    """Raised when required Cython module is unavailable."""
    pass


class ModuleSecurityError(ValueError):
    """Raised when module name fails security validation."""
    pass


# ============================================================================
# Secure Loader
# ============================================================================

class SecureCythonLoader:
    """
    Thread-safe loader with whitelist-only security.

    Features:
    - Validates module names against security rules
    - Thread-safe singleton loading pattern
    - Unique error IDs for troubleshooting
    - Lazy loading on first access

    Security Rules:
    - Module name must be valid Python identifier
    - Root package must be in whitelist
    - ASCII characters only
    """

    # Security pattern: valid Python module name
    VALID_NAME_PATTERN = re.compile(r'^[a-zA-Z_]\w*(\.[a-zA-Z_]\w*)*$')

    def __init__(self,
                 module_name: str,
                 allowed_packages: FrozenSet[str] = ALLOWED_PACKAGES):
        """Initialize loader with security validation.

        Args:
            module_name: Fully qualified module name to load
            allowed_packages: Whitelist of allowed root packages

        Raises:
            ModuleSecurityError: If module name fails validation
        """
        self._validate_module_name(module_name, allowed_packages)

        self.module_name = module_name
        self.allowed_packages = allowed_packages

        # Thread-safe state using Lock (Python 3.12 optimization)
        self._lock = threading.Lock()
        self._module: Optional[ModuleType] = None
        self._available: Optional[bool] = None
        self._error_id: Optional[str] = None
        self._import_error: Optional[ImportError] = None  # Cache the error

    def _validate_module_name(self,
                             module_name: str,
                             allowed_packages: FrozenSet[str]) -> None:
        """Validate module name against security rules.

        Args:
            module_name: Module name to validate
            allowed_packages: Allowed root packages

        Raises:
            ModuleSecurityError: If validation fails
        """
        # Check format and character set
        if not self.VALID_NAME_PATTERN.match(module_name):
            raise ModuleSecurityError(
                f"Invalid module name format: {module_name!r}"
            )

        if not module_name.isascii():
            raise ModuleSecurityError(
                f"Module name must be ASCII: {module_name!r}"
            )

        # Check whitelist
        root_package = module_name.split('.')[0]
        if root_package not in allowed_packages:
            raise ModuleSecurityError(
                f"Module root '{root_package}' not in allowed packages: "
                f"{sorted(allowed_packages)}"
            )

    def load(self) -> ModuleType:
        """Load module once, thread-safe with singleton pattern.

        Returns:
            Loaded module object

        Raises:
            CythonModuleUnavailable: If module cannot be imported
        """
        with self._lock:
            # First call: attempt import
            if self._available is None:
                self._attempt_import()

            # Return cached module or raise
            if self._available:
                return self._module

            raise CythonModuleUnavailable(
                f"Module '{self.module_name}' unavailable. "
                f"{BUILD_INSTRUCTIONS} "
                f"(Error ID: {self._error_id})"
            ) from self._import_error  # Python 3.12: preserve exception chain

    def _attempt_import(self) -> None:
        """Attempt to import the module and cache result."""
        try:
            # Check if already in sys.modules (could be imported elsewhere)
            if self.module_name in sys.modules:
                self._module = sys.modules[self.module_name]
                self._available = True
                logger.info(f"Found cached Cython module: {self.module_name}")
                return

            # Use standard import machinery - this is the Pythonic way
            self._module = importlib.import_module(self.module_name)
            self._available = True
            logger.info(f"Successfully loaded Cython module: {self.module_name}")

        except ImportError as e:
            self._available = False
            self._error_id = uuid.uuid4().hex[:8]
            self._import_error = e  # Cache for exception chaining
            logger.warning(
                f"Cython module unavailable [{self._error_id}]: {self.module_name}. "
                f"Reason: {e}. {BUILD_INSTRUCTIONS}"
            )
            logger.debug(f"Import error details [{self._error_id}]:", exc_info=True)

    @property
    def is_available(self) -> bool:
        """Check if module loaded successfully.

        Note: Does not trigger load, only returns cached availability state.

        Returns:
            True if module was loaded successfully, False if load failed,
            None if load has not been attempted yet.
        """
        return self._available is True

    def get_attribute(self, name: str):
        """Get attribute from loaded module.

        Args:
            name: Attribute name to retrieve

        Returns:
            The requested attribute

        Raises:
            CythonModuleUnavailable: If module not available
            AttributeError: If attribute doesn't exist
        """
        module = self.load()
        return getattr(module, name)


# ============================================================================
# Module Interface (PEP 562)
# ============================================================================

# Global loader singleton
_loader = SecureCythonLoader(MODULE_NAME)


def _create_unavailable_stub(name: str) -> Callable:
    """Create a stub function that raises informative error.

    Args:
        name: Name of the unavailable function/class

    Returns:
        Stub function that raises CythonModuleUnavailable
    """
    def _unavailable(*args, **kwargs):
        raise CythonModuleUnavailable(
            f"'{name}' requires Cython extensions. {BUILD_INSTRUCTIONS}"
        )

    _unavailable.__name__ = name
    _unavailable.__doc__ = (
        f"Unavailable stub for '{name}'. Cython extensions not built."
    )

    return _unavailable


def __getattr__(name: str):
    """
    Lazy load attributes on first access (PEP 562).

    This enables:
    - Lazy loading: Module only imported when actually used
    - IDE support: Attributes appear in autocomplete
    - Graceful degradation: Stubs provided when unavailable

    Args:
        name: Attribute name being accessed

    Returns:
        The requested attribute from the Cython module

    Raises:
        AttributeError: If attribute doesn't exist in this module

    Reference:
        PEP 562 - Module __getattr__ and __dir__
        https://www.python.org/dev/peps/pep-0562/
    """
    # Special case: availability check (doesn't trigger load)
    if name == 'is_cython_available':
        return _loader.is_available

    # Cython module exports
    if name in CYTHON_EXPORTS:
        try:
            attr = _loader.get_attribute(name)
            globals()[name] = attr  # Cache for faster subsequent access
            return attr
        except CythonModuleUnavailable:
            # Return stub that raises on use
            stub = _create_unavailable_stub(name)
            globals()[name] = stub  # Cache the stub too
            return stub

    # Unknown attribute
    raise AttributeError(
        f"module '{__name__}' has no attribute '{name}'"
    )


def __dir__() -> List[str]:
    """
    Support dir() and IDE autocomplete (PEP 562).

    Returns:
        List of available attributes in this module

    Reference:
        PEP 562 - Module __getattr__ and __dir__
    """
    return sorted(BASE_EXPORTS + CYTHON_EXPORTS)


def _build_all_list() -> List[str]:
    """
    Build __all__ list dynamically based on availability.

    This prevents linting errors for unresolved references while
    maintaining accurate export information.

    Returns:
        List of exported names
    """
    exports = BASE_EXPORTS.copy()

    # Add Cython exports if module is available
    if _loader.is_available:
        exports.extend(CYTHON_EXPORTS)

    return sorted(exports)


# Build export list
__all__ = _build_all_list()


# ============================================================================
# Convenience Functions
# ============================================================================

def check_cython_available(raise_on_unavailable: bool = False) -> bool:
    """Check if Cython extensions are available.

    Args:
        raise_on_unavailable: If True, raise exception instead of returning False

    Returns:
        True if Cython module is available, False otherwise

    Raises:
        CythonModuleUnavailable: If raise_on_unavailable=True and unavailable
    """
    if raise_on_unavailable and not _loader.is_available:
        raise CythonModuleUnavailable(
            f"Cython extensions not available. {BUILD_INSTRUCTIONS}"
        )

    return _loader.is_available


def get_error_id() -> Optional[str]:
    """Get the error ID from the last failed import attempt.

    Useful for troubleshooting and support requests.

    Returns:
        Error ID string if import failed, None if successful or not attempted
    """
    return _loader._error_id


def get_module_info() -> dict:
    """Get diagnostic information about the module.

    Returns:
        Dictionary with module status information
    """
    return {
        'module_name': _loader.module_name,
        'available': _loader.is_available,
        'error_id': _loader._error_id,
        'allowed_packages': sorted(_loader.allowed_packages),
        'exports': CYTHON_EXPORTS if _loader.is_available else [],
    }