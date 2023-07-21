# Copyright © 2023 Pathway

import math
from typing import Optional, Union

import pathway.internals.expression as expr
from pathway.internals import api


class NumericalNamespace:
    """A module containing methods related to numbers.
    They can be called using a `num` attribute of an expression.

    Typical use:

    >>> import pathway as pw
    >>> table = pw.debug.table_from_markdown(
    ...     '''
    ...      | v
    ...    1 | -1
    ... '''
    ... )
    >>> table_abs = table.select(v_abs=table.v.num.abs())
    """

    _expression: expr.ColumnExpression

    def __init__(self, expression: expr.ColumnExpression):
        self._expression = expression

    def abs(self) -> expr.ColumnExpression:
        """Returns the absolute value from a numerical value.

        Returns:
            Absolute value as float

        Example:

        >>> import pathway as pw
        >>> table = pw.debug.table_from_markdown(
        ...     '''
        ...      | v
        ...    1 | 1
        ...    2 | -1
        ...    3 | 2.5
        ...    4 | -2.5
        ... '''
        ... )
        >>> table_abs = table.select(v_abs=table.v.num.abs())
        >>> pw.debug.compute_and_print(table_abs, include_id=False)
        v_abs
        1.0
        1.0
        2.5
        2.5
        """

        return expr.MethodCallExpression(
            {
                int: lambda x: api.Expression.apply(abs, x),
                float: lambda x: api.Expression.apply(abs, x),
            },
            lambda dtypes: dtypes[0],
            "num.abs",
            self._expression,
        )

    def round(
        self, decimals: Union[expr.ColumnExpression, int] = 0
    ) -> expr.ColumnExpression:
        """Round the values in a column of a table to the specified number of decimals.

        Args:
            decimals: The number of decimal places to round to. It can be either an
            integer or a reference to another column. Defaults to 0.


        Returns:
            A new column with the values rounded to the specified number of decimals.

        Example:

        >>> import pathway as pw
        >>> table = pw.debug.table_from_markdown(
        ...     '''
        ...      | v
        ...    1 | -2.18
        ...    2 | -1.11
        ...    3 | 1
        ...    4 | 2.1
        ...    5 | 3.14
        ...    6 | 4.17
        ... '''
        ... )
        >>> table_round = table.select(v_round=table.v.num.round(1))
        >>> pw.debug.compute_and_print(table_round, include_id=False)
        v_round
        -2.2
        -1.1
        1.0
        2.1
        3.1
        4.2

        >>> import pathway as pw
        >>> table = pw.debug.table_from_markdown(
        ...     '''
        ...      | v      | precision
        ...    1 | 3      | 0
        ...    2 | 3.1    | 1
        ...    3 | 3.14   | 1
        ...    4 | 3.141  | 2
        ...    5 | 3.1415 | 2
        ... '''
        ... )
        >>> table_round = table.select(v_round=table.v.num.round(pw.this.precision))
        >>> pw.debug.compute_and_print(table_round, include_id=False)
        v_round
        3.0
        3.1
        3.1
        3.14
        3.14
        """

        return expr.MethodCallExpression(
            {
                (int, int): lambda x, y: api.Expression.apply(round, x, y),
                (float, int): lambda x, y: api.Expression.apply(round, x, y),
            },
            lambda dtypes: dtypes[0],
            "num.round",
            self._expression,
            decimals,
        )

    def fill_na(self, default_value: Union[int, float]) -> expr.ColumnExpression:
        """Fill the missing values (None or NaN) in a column of a table with a specified default value.

        Args:
            default_value (float): The value to fill in for the missing values.

        Returns:
            A new column with the missing values filled with the specified default value.

        Example:

        >>> import pathway as pw
        >>> table = pw.debug.table_from_markdown(
        ...     '''
        ...      | v
        ...    1 | 1
        ...    2 | 2.0
        ...    3 | None
        ...    4 | 3.5
        ... '''
        ... )
        >>> table_fill_na = table.select(v_filled=table.v.num.fill_na(0))
        >>> pw.debug.compute_and_print(table_fill_na, include_id=False)
        v_filled
        0.0
        1.0
        2.0
        3.5
        """

        def get_return_type(input_type):
            if input_type == Optional[int]:
                return int
            if input_type == Optional[float]:
                return float
            return input_type

        # XXX Update to api.Expression.if_else when a isnan operator is supported.
        return expr.MethodCallExpression(
            {
                int: lambda x: x,
                float: lambda x: api.Expression.apply(
                    lambda y: float(default_value) if math.isnan(y) else y, x
                ),
                Optional[int]: lambda x: api.Expression.apply(
                    lambda y: int(default_value) if y is None else y, x
                ),
                Optional[float]: lambda x: api.Expression.apply(
                    lambda y: float(default_value)
                    if ((y is None) or math.isnan(y))
                    else y,
                    x,
                ),
            },
            lambda dtypes: get_return_type(dtypes[0]),
            "num.fill_na",
            self._expression,
        )
