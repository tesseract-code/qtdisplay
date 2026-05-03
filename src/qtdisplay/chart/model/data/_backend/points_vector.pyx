# distutils: language = c++
# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True
# cython: initializedcheck=False

"""
High-performance circular buffer for real-time charting.

Key optimizations based on Cython best practices:
1. Compiler directives for maximum speed
2. Typed memoryviews instead of numpy arrays
3. Minimal Python object interaction
4. Strategic GIL release only where safe (pure C operations)
5. C-level operations in tight loops
"""

cimport cython
from libc.math cimport INFINITY, fabs
import numpy as np
cimport numpy as cnp
from PyQt6.QtCore import QPointF

cnp.import_array()

# Type alias for consistency
ctypedef cnp.float64_t DTYPE_t


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.nonecheck(False)
def create_qpointf(cnp.ndarray[DTYPE_t, ndim=1] xs not None,
                   cnp.ndarray[DTYPE_t, ndim=1] ys not None):
    """
    Optimized QPointF list creation from numpy arrays.

    High-performance utility for converting numpy arrays to QPointF lists
    with minimal Python overhead. Uses typed memoryviews and pre-allocated
    result list for maximum speed.

    Args:
        xs: X coordinates as numpy float64 array
        ys: Y coordinates as numpy float64 array

    Returns:
        List of QPointF objects (empty list if input is empty)

    Performance: ~50-100ns per point for large arrays (N>1000)

    Example:
        >>> x = np.array([1.0, 2.0, 3.0])
        >>> y = np.array([4.0, 5.0, 6.0])
        >>> points = create_qpointf(x, y)
    """
    cdef Py_ssize_t n = xs.shape[0]

    if n == 0:
        return []

    if ys.shape[0] != n:
        raise ValueError(f"Array length mismatch: xs={n}, ys={ys.shape[0]}")

    # Use typed memoryviews for zero-overhead access
    cdef DTYPE_t[:] mv_x = xs
    cdef DTYPE_t[:] mv_y = ys

    # Pre-allocate result list
    cdef list result = [None] * n
    cdef Py_ssize_t i

    # QPointF creation requires GIL (Python objects)
    for i in range(n):
        result[i] = QPointF(mv_x[i], mv_y[i])

    return result


cdef class PointsVector:
    """
    Optimized points vector backend with synchronized QPointF cache.

    Performance improvements:
    - Uses typed memoryviews for zero-overhead array access
    - Strategic GIL release for pure numeric operations
    - Reduced bounds checking via compiler directives
    - Direct QPointF manipulation in separate passes
    """

    cdef:
        cnp.float64_t[:, ::1] _data  # C-contiguous memoryview
        Py_ssize_t _cursor
        Py_ssize_t _count
        Py_ssize_t _max_size
        list _qpoint_cache
        bint _bounds_dirty
        cnp.float64_t _min_x, _max_x, _min_y, _max_y

    def __cinit__(self, Py_ssize_t max_size=1000):
        if max_size <= 0:
            raise ValueError("max_size must be positive")

        self._max_size = max_size
        # Pre-allocate with C-contiguous layout for cache efficiency
        cdef cnp.ndarray[cnp.float64_t, ndim=2] data_arr = np.empty(
            (max_size, 2), dtype=np.float64, order='C'
        )
        self._data = data_arr

        # Pre-allocate QPointF cache
        self._qpoint_cache = [QPointF() for _ in range(max_size)]

        self._cursor = 0
        self._count = 0
        self._bounds_dirty = True
        self._min_x = INFINITY
        self._max_x = -INFINITY
        self._min_y = INFINITY
        self._max_y = -INFINITY

    @cython.boundscheck(False)
    @cython.wraparound(False)
    @cython.cdivision(True)
    cpdef void extend(self, cnp.float64_t[:] x_values, cnp.float64_t[:] y_values):
        """
        Fast bulk insert with split GIL/nogil operations.

        Strategy: Update numeric data without GIL, then update QPointF with GIL.
        """
        cdef Py_ssize_t n = x_values.shape[0]
        cdef Py_ssize_t i, idx
        cdef cnp.float64_t x, y
        cdef object point

        if n == 0:
            return

        # Phase 1: Update numeric data (can be done without GIL)
        with nogil:
            for i in range(n):
                idx = (self._cursor + i) % self._max_size
                self._data[idx, 0] = x_values[i]
                self._data[idx, 1] = y_values[i]

        # Phase 2: Update QPointF cache (requires GIL - Python objects)
        for i in range(n):
            idx = (self._cursor + i) % self._max_size
            point = self._qpoint_cache[idx]
            point.setX(self._data[idx, 0])
            point.setY(self._data[idx, 1])

        # Update state
        self._cursor = (self._cursor + n) % self._max_size
        self._count = min(self._count + n, self._max_size)
        self._bounds_dirty = True

    @cython.boundscheck(False)
    @cython.wraparound(False)
    cpdef void append(self, cnp.float64_t x, cnp.float64_t y):
        """
        Fast single-point append.

        Optimized for hot path - updates both data and QPointF efficiently.
        """
        cdef Py_ssize_t idx = self._cursor
        cdef object point

        # Update data array
        self._data[idx, 0] = x
        self._data[idx, 1] = y

        # Update QPointF cache (requires GIL)
        point = self._qpoint_cache[idx]
        point.setX(x)
        point.setY(y)

        # Update cursor with wrap
        self._cursor = (idx + 1) % self._max_size

        # Update count
        if self._count < self._max_size:
            self._count += 1
        else:
            self._bounds_dirty = True

        # Incremental bounds update for append
        if not self._bounds_dirty:
            if x < self._min_x:
                self._min_x = x
            if x > self._max_x:
                self._max_x = x
            if y < self._min_y:
                self._min_y = y
            if y > self._max_y:
                self._max_y = y

    cpdef tuple get_bounds(self):
        """Get the bounds of all points."""
        if self._bounds_dirty:
            self._recalculate_bounds()
        return (self._min_x, self._max_x), (self._min_y, self._max_y)

    @cython.boundscheck(False)
    @cython.wraparound(False)
    cdef void _recalculate_bounds(self):
        """
        Recalculate bounds using nogil for pure numeric computation.

        Uses direct memoryview access for maximum speed.
        """
        cdef Py_ssize_t i, count
        cdef cnp.float64_t x, y
        cdef cnp.float64_t min_x, max_x, min_y, max_y

        count = self._count
        if count == 0:
            self._min_x = self._max_x = self._min_y = self._max_y = 0.0
            self._bounds_dirty = False
            return

        min_x = INFINITY
        max_x = -INFINITY
        min_y = INFINITY
        max_y = -INFINITY

        # Pure numeric loop - can run without GIL
        with nogil:
            for i in range(count):
                x = self._data[i, 0]
                y = self._data[i, 1]

                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y

        # Update instance variables (requires GIL but happens outside loop)
        self._min_x = min_x
        self._max_x = max_x
        self._min_y = min_y
        self._max_y = max_y
        self._bounds_dirty = False

    @cython.boundscheck(False)
    @cython.wraparound(False)
    cpdef tuple to_arrays(self):
        """
        Return numpy arrays with zero-copy when possible.

        Optimization: Returns views of internal data when not wrapped.
        """
        cdef Py_ssize_t i, j, idx
        cdef cnp.ndarray[cnp.float64_t, ndim=1] x_arr, y_arr
        cdef cnp.float64_t[:] x_view, y_view

        if self._count == 0:
            return np.array([], dtype=np.float64), np.array([], dtype=np.float64)

        if self._count < self._max_size:
            # Fast path: return view of contiguous data (zero-copy)
            x_arr = np.asarray(self._data[:self._count, 0])
            y_arr = np.asarray(self._data[:self._count, 1])
        else:
            # Wrapped buffer: need to reorder
            x_arr = np.empty(self._max_size, dtype=np.float64)
            y_arr = np.empty(self._max_size, dtype=np.float64)
            x_view = x_arr
            y_view = y_arr

            idx = self._cursor
            j = 0

            # Copy in correct order - pure numeric, can use nogil
            with nogil:
                for i in range(idx, self._max_size):
                    x_view[j] = self._data[i, 0]
                    y_view[j] = self._data[i, 1]
                    j += 1

                for i in range(0, idx):
                    x_view[j] = self._data[i, 0]
                    y_view[j] = self._data[i, 1]
                    j += 1

        return x_arr, y_arr

    cpdef list to_qpointf(self):
        """
        Return QPointF list - O(1) for non-wrapped, O(n) for wrapped.

        Optimization: Returns slice view when possible.
        """
        cdef Py_ssize_t n, start_idx, i, j
        cdef list cache, result

        if self._count == 0:
            return []

        n = self._count
        cache = self._qpoint_cache

        if self._count < self._max_size:
            # Fast path: return slice (Python list slice is O(k) but cheap)
            return cache[:n]
        else:
            # Wrapped: reorder references (Python list operations require GIL)
            result = [None] * n
            start_idx = self._cursor
            j = 0

            for i in range(start_idx, self._max_size):
                result[j] = cache[i]
                j += 1

            for i in range(start_idx):
                result[j] = cache[i]
                j += 1

            return result

    cpdef void clear(self):
        """Clear all points."""
        self._cursor = 0
        self._count = 0
        self._bounds_dirty = True
        self._min_x = INFINITY
        self._max_x = -INFINITY
        self._min_y = INFINITY
        self._max_y = -INFINITY

    cpdef Py_ssize_t get_count(self):
        """Get the number of points."""
        return self._count

    def __len__(self):
        return self.get_count()

    def __bool__(self):
        return self._count > 0

    property bounds:
        def __get__(self):
            return self.get_bounds()

    property size:
        def __get__(self):
            return self._max_size


cdef class DualPointVector:
    """
    Dual vector for area charts using two synchronized PointsVector instances.

    Optimization: Delegates to optimized PointsVector for all operations.
    """

    cdef:
        PointsVector _upper_vector
        PointsVector _lower_vector
        Py_ssize_t _max_size

    def __cinit__(self, Py_ssize_t max_size=1000):
        if max_size <= 0:
            raise ValueError("max_size must be positive")

        self._max_size = max_size
        self._upper_vector = PointsVector(max_size)
        self._lower_vector = PointsVector(max_size)

    cpdef void append(self, cnp.float64_t x, cnp.float64_t y_upper, cnp.float64_t y_lower):
        """Synchronized append to both vectors."""
        self._upper_vector.append(x, y_upper)
        self._lower_vector.append(x, y_lower)

    cpdef tuple get_bounds(self):
        """Get combined bounds from both vectors."""
        cdef tuple upper_bounds, lower_bounds
        cdef tuple x_bounds_u, y_bounds_u, x_bounds_l, y_bounds_l
        cdef cnp.float64_t min_x, max_x, min_y, max_y

        if self._upper_vector.get_count() == 0:
            return (0.0, 0.0), (0.0, 0.0)

        upper_bounds = self._upper_vector.get_bounds()
        lower_bounds = self._lower_vector.get_bounds()

        x_bounds_u = upper_bounds[0]
        y_bounds_u = upper_bounds[1]
        x_bounds_l = lower_bounds[0]
        y_bounds_l = lower_bounds[1]

        # Combine bounds
        min_x = x_bounds_u[0] if x_bounds_u[0] < x_bounds_l[0] else x_bounds_l[0]
        max_x = x_bounds_u[1] if x_bounds_u[1] > x_bounds_l[1] else x_bounds_l[1]
        min_y = y_bounds_u[0] if y_bounds_u[0] < y_bounds_l[0] else y_bounds_l[0]
        max_y = y_bounds_u[1] if y_bounds_u[1] > y_bounds_l[1] else y_bounds_l[1]

        return (min_x, max_x), (min_y, max_y)

    cpdef tuple get_upper_arrays(self):
        """Get x and y arrays for upper points."""
        return self._upper_vector.to_arrays()

    cpdef tuple get_lower_arrays(self):
        """Get x and y arrays for lower points."""
        return self._lower_vector.to_arrays()

    cpdef tuple get_y_range(self):
        """Get the current y range from combined bounds."""
        cdef tuple bounds = self.get_bounds()
        cdef tuple y_bounds = bounds[1]
        return y_bounds[0], y_bounds[1]

    cpdef void clear(self):
        """Clear all points from both vectors."""
        self._upper_vector.clear()
        self._lower_vector.clear()

    cpdef Py_ssize_t get_count(self):
        """Get the number of points."""
        return self._upper_vector.get_count()

    def __len__(self):
        return self.get_count()

    def __bool__(self):
        return self._upper_vector.get_count() > 0

    property bounds:
        def __get__(self):
            return self.get_bounds()

    property y_range:
        def __get__(self):
            return self.get_y_range()

    property size:
        def __get__(self):
            return self._max_size

    property current_index:
        def __get__(self):
            return self._upper_vector._cursor

    cpdef list get_upper_qpointf(self):
        """Get QPointF list for upper series."""
        return self._upper_vector.to_qpointf()

    cpdef list get_lower_qpointf(self):
        """Get QPointF list for lower series."""
        return self._lower_vector.to_qpointf()

    cpdef void extend(self, cnp.float64_t[:] x_values,
                     cnp.float64_t[:] y_upper_values,
                     cnp.float64_t[:] y_lower_values):
        """Synchronized bulk insert to both vectors."""
        if x_values.shape[0] != y_upper_values.shape[0] or x_values.shape[0] != y_lower_values.shape[0]:
            raise ValueError("All input arrays must have the same length")

        self._upper_vector.extend(x_values, y_upper_values)
        self._lower_vector.extend(x_values, y_lower_values)



cdef class SeriesCache:
    """
    Optimized cache with checksum-based validation for sliding windows.
    """

    cdef:
        DTYPE_t[:] x_array      # Memoryview (does not require GIL for indexing)
        DTYPE_t[:] y_array
        cnp.int64_t[:] sort_indices
        object points_list      # Python list (requires GIL)
        Py_ssize_t point_count
        DTYPE_t _min_x, _max_x
        cnp.int64_t _checksum

    def __cinit__(self,
                  cnp.ndarray[DTYPE_t, ndim=1] x_array,
                  cnp.ndarray[DTYPE_t, ndim=1] y_array,
                  cnp.ndarray[cnp.int64_t, ndim=1] sort_indices,
                  object points_list,
                  Py_ssize_t point_count):
        """Initialize cache."""
        self.x_array = x_array
        self.y_array = y_array
        self.sort_indices = sort_indices
        self.points_list = points_list
        self.point_count = point_count

        if point_count > 0:
            self._min_x = x_array[sort_indices[0]]
            self._max_x = x_array[sort_indices[point_count - 1]]
            self._checksum = self._compute_checksum()

    @cython.boundscheck(False)
    @cython.wraparound(False)
    cdef cnp.int64_t _compute_checksum(self):
        """Compute fast checksum from boundary values."""
        cdef DTYPE_t x_first, x_last, y_first, y_last
        cdef cnp.int64_t result

        if self.point_count == 0:
            return 0

        # Memoryview access does not require GIL
        x_first = self.x_array[0]
        x_last = self.x_array[self.point_count - 1]
        y_first = self.y_array[0]
        y_last = self.y_array[self.point_count - 1]

        # Simple hash combining boundary values
        result = (<cnp.int64_t>(x_first * 31) ^
                 <cnp.int64_t>(x_last * 37) ^
                 <cnp.int64_t>(y_first * 41) ^
                 <cnp.int64_t>(y_last * 43))

        return result

    cpdef bint is_valid(self, Py_ssize_t current_count, object points_list):
        """
        Fast validation with checksum comparison.
        """
        cdef cnp.int64_t current_checksum
        cdef DTYPE_t x_first, x_last, y_first, y_last
        cdef object point

        if self.point_count != current_count:
            return False

        if current_count == 0:
            return True

        # Access Python list (requires GIL)
        point = points_list[0]
        x_first = point.x()
        point = points_list[current_count - 1]
        x_last = point.x()

        point = points_list[0]
        y_first = point.y()
        point = points_list[current_count - 1]
        y_last = point.y()

        current_checksum = (<cnp.int64_t>(x_first * 31) ^
                           <cnp.int64_t>(x_last * 37) ^
                           <cnp.int64_t>(y_first * 41) ^
                           <cnp.int64_t>(y_last * 43))

        return current_checksum == self._checksum


cdef class NearestPointFinder:
    """
    Ultra-optimized nearest point finder using Cython.
    """

    cdef:
        Py_ssize_t search_radius

    def __cinit__(self, Py_ssize_t search_radius=50):
        self.search_radius = search_radius

    @cython.boundscheck(False)
    @cython.wraparound(False)
    @cython.cdivision(True)
    cpdef object find_nearest_1d(self, DTYPE_t x_target, SeriesCache cache):
        """
        Fastest 1D nearest neighbor - optimized binary search.
        """
        cdef Py_ssize_t n = cache.point_count
        cdef Py_ssize_t idx, left_idx, right_idx, closest_idx
        cdef DTYPE_t left_dist, right_dist
        cdef DTYPE_t[:] x_arr_view = cache.x_array
        cdef cnp.int64_t[:] sort_indices_view = cache.sort_indices
        cdef Py_ssize_t left, right, mid
        cdef cnp.int64_t sorted_idx
        cdef DTYPE_t sorted_x_val

        if n == 0:
            return None

        if n == 1:
            return cache.points_list[cache.sort_indices[0]]

        # Fast bounds check
        if x_target <= cache._min_x:
            return cache.points_list[cache.sort_indices[0]]
        if x_target >= cache._max_x:
            return cache.points_list[cache.sort_indices[n - 1]]

        # Binary search - manually index through sort_indices
        left = 0
        right = n
        while left < right:
            mid = (left + right) >> 1
            sorted_idx = sort_indices_view[mid]
            sorted_x_val = x_arr_view[sorted_idx]
            if sorted_x_val < x_target:
                left = mid + 1
            else:
                right = mid
        idx = left

        # Compare neighbors
        left_idx = idx - 1
        right_idx = idx

        sorted_idx = sort_indices_view[left_idx]
        left_dist = x_target - x_arr_view[sorted_idx]

        sorted_idx = sort_indices_view[right_idx]
        right_dist = x_arr_view[sorted_idx] - x_target

        closest_idx = left_idx if left_dist <= right_dist else right_idx

        # Return Python object
        return cache.points_list[sort_indices_view[closest_idx]]

    @cython.boundscheck(False)
    @cython.wraparound(False)
    @cython.cdivision(True)
    cpdef tuple find_nearest_2d(self, DTYPE_t x_target, DTYPE_t y_target,
                                SeriesCache cache):
        """
        Optimized 2D nearest neighbor with adaptive windowed search.
        """
        cdef Py_ssize_t n = cache.point_count
        cdef Py_ssize_t idx, start_idx, end_idx, radius
        cdef Py_ssize_t i, min_idx
        cdef DTYPE_t[:] x_arr_view = cache.x_array
        cdef DTYPE_t[:] y_arr_view = cache.y_array
        cdef cnp.int64_t[:] sort_indices_view = cache.sort_indices
        cdef DTYPE_t spacing, scale
        cdef DTYPE_t dx, dy, dist_sq, min_dist_sq
        cdef cnp.int64_t original_idx, sorted_idx
        cdef Py_ssize_t left, right, mid
        cdef DTYPE_t sorted_x_val

        if n == 0:
            return None, INFINITY

        if n == 1:
            dx = cache.x_array[0] - x_target
            dy = cache.y_array[0] - y_target
            return cache.points_list[0], dx * dx + dy * dy

        # Small dataset: brute force
        if n < 100:
            return self._brute_force_2d(x_target, y_target, cache)

        # Binary search - manually index through sort_indices
        left = 0
        right = n
        while left < right:
            mid = (left + right) >> 1
            sorted_idx = sort_indices_view[mid]
            sorted_x_val = x_arr_view[sorted_idx]
            if sorted_x_val < x_target:
                left = mid + 1
            else:
                right = mid
        idx = left

        # Adaptive radius based on local spacing
        radius = self.search_radius
        if 0 < idx < n:
            sorted_idx = sort_indices_view[idx]
            sorted_x_val = x_arr_view[sorted_idx]

            sorted_idx = sort_indices_view[idx - 1]
            spacing = sorted_x_val - x_arr_view[sorted_idx]

            if spacing > 0:
                scale = min(2.0, 1.0 / (spacing + 0.1))
                radius = <Py_ssize_t>(self.search_radius * scale)

        # Clamp window
        start_idx = max(0, idx - radius)
        end_idx = min(n, idx + radius)

        if start_idx >= end_idx:
            return None, INFINITY

        # Find minimum distance in window
        min_dist_sq = INFINITY
        min_idx = start_idx

        for i in range(start_idx, end_idx):
            original_idx = sort_indices_view[i]
            dx = x_arr_view[original_idx] - x_target
            dy = y_arr_view[original_idx] - y_target
            dist_sq = dx * dx + dy * dy

            if dist_sq < min_dist_sq:
                min_dist_sq = dist_sq
                min_idx = i

        original_idx = sort_indices_view[min_idx]
        return cache.points_list[original_idx], min_dist_sq

    @cython.boundscheck(False)
    @cython.wraparound(False)
    cdef tuple _brute_force_2d(self, DTYPE_t x_target, DTYPE_t y_target,
                                SeriesCache cache):
        """Fast brute force for small datasets."""
        cdef Py_ssize_t n = cache.point_count
        cdef Py_ssize_t i, min_idx = 0
        cdef DTYPE_t dx, dy, dist_sq, min_dist_sq
        cdef DTYPE_t[:] x_arr = cache.x_array
        cdef DTYPE_t[:] y_arr = cache.y_array

        min_dist_sq = INFINITY

        for i in range(n):
            dx = x_arr[i] - x_target
            dy = y_arr[i] - y_target
            dist_sq = dx * dx + dy * dy

            if dist_sq < min_dist_sq:
                min_dist_sq = dist_sq
                min_idx = i

        return cache.points_list[min_idx], min_dist_sq


cdef class PointCacheManager:
    """
    Centralized cache manager with Cython-optimized operations.
    """

    cdef:
        dict _cache

    def __cinit__(self):
        self._cache = {}

    cpdef SeriesCache get_or_build(self, str series_name, object series):
        """
        Get cached data or build if invalid/missing.
        """
        cdef object points = series.points()
        cdef Py_ssize_t current_count = len(points)
        cdef SeriesCache cache

        if current_count == 0:
            return None

        # Check if cache exists and is valid
        if series_name in self._cache:
            cache = self._cache[series_name]
            if cache.is_valid(current_count, points):
                return cache

        # Build new cache
        return self._build_cache(series_name, points, current_count)

    @cython.boundscheck(False)
    @cython.wraparound(False)
    cdef SeriesCache _build_cache(self, str series_name, object points,
                                   Py_ssize_t count):
        """
        Build cache with optimized array extraction.
        """
        cdef cnp.ndarray[DTYPE_t, ndim=1] x_array = np.empty(count, dtype=np.float64)
        cdef cnp.ndarray[DTYPE_t, ndim=1] y_array = np.empty(count, dtype=np.float64)
        cdef cnp.ndarray[cnp.int64_t, ndim=1] sort_indices
        cdef DTYPE_t[:] x_view = x_array
        cdef DTYPE_t[:] y_view = y_array
        cdef Py_ssize_t i
        cdef object point

        # Extract coordinates (requires GIL for Python list/QPointF access)
        for i in range(count):
            point = points[i]
            x_view[i] = point.x()
            y_view[i] = point.y()

        # Sort by x for binary search
        sort_indices = np.argsort(x_array)

        # Create cache
        cache = SeriesCache(x_array, y_array, sort_indices, points, count)
        self._cache[series_name] = cache

        return cache

    cpdef void invalidate(self, str series_name=None):
        """Invalidate cached data."""
        if series_name is None:
            self._cache.clear()
        elif series_name in self._cache:
            del self._cache[series_name]

    cpdef dict get_stats(self):
        """Get cache statistics."""
        cdef Py_ssize_t total_points = 0
        cdef Py_ssize_t total_memory = 0
        cdef SeriesCache cache
        cdef DTYPE_t[:] x_view
        cdef DTYPE_t[:] y_view
        cdef cnp.int64_t[:] sort_view

        for cache in self._cache.values():
            total_points += cache.point_count
            x_view = cache.x_array
            y_view = cache.y_array
            sort_view = cache.sort_indices
            total_memory += (x_view.nbytes + y_view.nbytes + sort_view.nbytes)

        return {
            'cached_series': len(self._cache),
            'total_points': total_points,
            'memory_bytes': total_memory,
            'memory_kb': total_memory / 1024
        }

@cython.boundscheck(False)
@cython.wraparound(False)
@cython.cdivision(True)
def lttb_downsample(cnp.ndarray[DTYPE_t, ndim=1] x_data not None,
                    cnp.ndarray[DTYPE_t, ndim=1] y_data not None,
                    Py_ssize_t threshold):
    """
    Largest Triangle Three Buckets (LTTB) downsampling algorithm.

    Preserves visual appearance while reducing data points. Best algorithm
    for time-series visualization.

    Args:
        x_data: X coordinates (must be sorted)
        y_data: Y coordinates
        threshold: Target number of points (typically 500-2000)

    Returns:
        Tuple of (downsampled_x, downsampled_y, selected_indices)

    Performance: ~0.5ms for 10,000 → 500 points

    Example:
        >>> x = np.linspace(0, 1000, 10000)
        >>> y = np.sin(x / 100) + np.random.randn(10000) * 0.1
        >>> x_down, y_down, indices = lttb_downsample(x, y, 500)
    """
    cdef Py_ssize_t data_length = x_data.shape[0]

    if data_length <= threshold or threshold < 3:
        # Return copy if no downsampling needed
        indices = np.arange(data_length, dtype=np.int64)
        return x_data.copy(), y_data.copy(), indices

    cdef DTYPE_t[:] x_view = x_data
    cdef DTYPE_t[:] y_view = y_data

    # Pre-allocate output arrays
    cdef cnp.ndarray[DTYPE_t, ndim=1] sampled_x = np.empty(threshold, dtype=np.float64)
    cdef cnp.ndarray[DTYPE_t, ndim=1] sampled_y = np.empty(threshold, dtype=np.float64)
    cdef cnp.ndarray[cnp.int64_t, ndim=1] sampled_indices = np.empty(threshold, dtype=np.int64)

    cdef DTYPE_t[:] sampled_x_view = sampled_x
    cdef DTYPE_t[:] sampled_y_view = sampled_y
    cdef cnp.int64_t[:] indices_view = sampled_indices

    cdef Py_ssize_t sampled_index = 0
    cdef DTYPE_t bucket_size = <DTYPE_t>(data_length - 2) / <DTYPE_t>(threshold - 2)

    cdef Py_ssize_t a = 0  # Always include first point
    cdef Py_ssize_t bucket_start, bucket_end, next_bucket_start, next_bucket_end
    cdef Py_ssize_t i, max_area_point
    cdef DTYPE_t avg_x, avg_y, area, max_area
    cdef DTYPE_t point_ax, point_ay, point_bx, point_by
    cdef Py_ssize_t range_start, range_end, range_length

    # Always include first point
    sampled_x_view[sampled_index] = x_view[0]
    sampled_y_view[sampled_index] = y_view[0]
    indices_view[sampled_index] = 0
    sampled_index += 1

    # Process middle buckets
    for i in range(threshold - 2):
        # Current bucket
        bucket_start = <Py_ssize_t>((i + 1) * bucket_size) + 1
        bucket_end = <Py_ssize_t>((i + 2) * bucket_size) + 1
        if bucket_end > data_length:
            bucket_end = data_length

        # Calculate average point in next bucket (point C)
        next_bucket_start = bucket_end
        next_bucket_end = <Py_ssize_t>((i + 3) * bucket_size) + 1
        if next_bucket_end > data_length:
            next_bucket_end = data_length

        avg_x = 0.0
        avg_y = 0.0
        range_start = next_bucket_start
        range_end = next_bucket_end
        range_length = range_end - range_start

        if range_length > 0:
            for j in range(range_start, range_end):
                avg_x += x_view[j]
                avg_y += y_view[j]
            avg_x /= range_length
            avg_y /= range_length
        else:
            # Last bucket
            avg_x = x_view[data_length - 1]
            avg_y = y_view[data_length - 1]

        # Find point in current bucket with largest triangle area
        max_area = -1.0
        max_area_point = bucket_start

        point_ax = x_view[a]
        point_ay = y_view[a]

        for j in range(bucket_start, bucket_end):
            point_bx = x_view[j]
            point_by = y_view[j]

            # Calculate triangle area using cross product
            # Area = 0.5 * |x_a(y_b - y_c) + x_b(y_c - y_a) + x_c(y_a - y_b)|
            area = fabs(
                point_ax * (point_by - avg_y) +
                point_bx * (avg_y - point_ay) +
                avg_x * (point_ay - point_by)
            )

            if area > max_area:
                max_area = area
                max_area_point = j

        # Add selected point
        sampled_x_view[sampled_index] = x_view[max_area_point]
        sampled_y_view[sampled_index] = y_view[max_area_point]
        indices_view[sampled_index] = max_area_point
        sampled_index += 1

        a = max_area_point  # This point is the next starting point

    # Always include last point
    sampled_x_view[sampled_index] = x_view[data_length - 1]
    sampled_y_view[sampled_index] = y_view[data_length - 1]
    indices_view[sampled_index] = data_length - 1

    return sampled_x, sampled_y, sampled_indices


@cython.boundscheck(False)
@cython.wraparound(False)
def adaptive_threshold(Py_ssize_t data_length, Py_ssize_t screen_width_pixels):
    """
    Calculate optimal downsampling threshold based on screen width.

    Rule of thumb: 2-4 points per pixel for smooth curves.

    Args:
        data_length: Total number of data points
        screen_width_pixels: Chart width in pixels

    Returns:
        Optimal threshold (clamped between 100 and data_length)
    """
    cdef Py_ssize_t threshold = screen_width_pixels * 3

    # Clamp to reasonable bounds
    if threshold < 100:
        threshold = 100
    if threshold > data_length:
        threshold = data_length

    return threshold


@cython.boundscheck(False)
@cython.wraparound(False)
def downsample_to_qpointf(cnp.ndarray[DTYPE_t, ndim=1] x_data not None,
                          cnp.ndarray[DTYPE_t, ndim=1] y_data not None,
                          Py_ssize_t threshold):
    """
    Downsample and convert directly to QPointF list.

    Convenience function combining LTTB downsampling with QPointF creation.

    Args:
        x_data: X coordinates
        y_data: Y coordinates
        threshold: Target number of points

    Returns:
        List of QPointF objects (downsampled)
    """
    cdef cnp.ndarray[DTYPE_t, ndim=1] x_down, y_down
    cdef cnp.ndarray[cnp.int64_t, ndim=1] indices

    x_down, y_down, indices = lttb_downsample(x_data, y_data, threshold)

    cdef Py_ssize_t n = x_down.shape[0]
    cdef DTYPE_t[:] x_view = x_down
    cdef DTYPE_t[:] y_view = y_down
    cdef list result = [None] * n
    cdef Py_ssize_t i

    for i in range(n):
        result[i] = QPointF(x_view[i], y_view[i])

    return result


class ChartDownsamplingStrategy:
    """
    Strategy for when to downsample chart data.

    Guidelines:
    - Always downsample for rendering if N > 2000
    - Keep full data for tooltip nearest-neighbor search
    - Dynamically adjust threshold based on zoom level
    """

    def __init__(self, chart_width_pixels=800):
        self.chart_width_pixels = chart_width_pixels
        self.full_data_x = None
        self.full_data_y = None
        self.downsampled_indices = None

    def should_downsample(self, data_length: int) -> bool:
        """Determine if downsampling is needed."""
        threshold = adaptive_threshold(data_length, self.chart_width_pixels)
        return data_length > threshold

    def get_threshold(self, data_length: int) -> int:
        """Get optimal threshold for current chart width."""
        return adaptive_threshold(data_length, self.chart_width_pixels)

    def downsample_for_rendering(self, x_data, y_data):
        """
        Downsample data for chart rendering.

        Stores full data for tooltip lookups.
        """
        # Store full data for tooltips
        self.full_data_x = x_data
        self.full_data_y = y_data

        threshold = self.get_threshold(len(x_data))

        if not self.should_downsample(len(x_data)):
            return x_data, y_data, np.arange(len(x_data))

        x_down, y_down, indices = lttb_downsample(
            np.asarray(x_data, dtype=np.float64),
            np.asarray(y_data, dtype=np.float64),
            threshold
        )

        self.downsampled_indices = indices
        return x_down, y_down, indices

    def get_full_data_for_tooltip(self):
        """Get full resolution data for accurate tooltip lookups."""
        return self.full_data_x, self.full_data_y
