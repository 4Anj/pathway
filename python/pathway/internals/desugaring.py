# Copyright © 2023 Pathway

from __future__ import annotations

from abc import abstractmethod
from functools import wraps
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Tuple,
    TypeVar,
    cast,
)

from pathway.internals import expression as expr
from pathway.internals.expression_visitor import IdentityTransform
from pathway.internals.helpers import function_spec, with_optional_kwargs
from pathway.internals.row_transformer import RowTransformer

if TYPE_CHECKING:
    from pathway.internals import groupby, table, thisclass


class DesugaringTransform(IdentityTransform):
    def eval_any(self, expression, **kwargs):
        return expression


class ThisDesugaring(DesugaringTransform):
    substitution: Dict[thisclass.ThisMetaclass, table.Joinable]

    def __init__(self, substitution: Dict[thisclass.ThisMetaclass, table.Joinable]):
        self.substitution = substitution

    def eval_column_val(self, expression: expr.ColumnReference) -> expr.ColumnReference:
        table = self._desugar_table(expression.table)
        return table[expression.name]

    def eval_pointer(
        self, expression: expr.PointerExpression
    ) -> expr.PointerExpression:
        args = [self.eval_expression(arg) for arg in expression._args]
        optional = expression._optional
        desugared_table = self._desugar_table(expression._table)
        from pathway.internals import table

        return expr.PointerExpression(
            cast(table.Table, desugared_table), *args, optional=optional
        )

    def _desugar_table(
        self, table: table.Joinable | thisclass.ThisMetaclass
    ) -> table.Joinable:
        from pathway.internals import thisclass

        if isinstance(table, thisclass.ThisMetaclass):
            return table._eval_substitution(self.substitution)
        else:
            return table


class SubstitutionDesugaring(DesugaringTransform):
    substitution: Dict[expr.InternalColRef, expr.ColumnExpression]

    def __init__(self, substitution: Dict[expr.InternalColRef, expr.ColumnExpression]):
        self.substitution = substitution

    def eval_column_val(self, expression: expr.ColumnReference) -> expr.ColumnExpression:  # type: ignore
        return self.substitution.get(expression._to_internal(), expression)


class TableSubstitutionDesugaring(DesugaringTransform):
    """Maps all references to tables according to `table_substitution` dictionary."""

    def __init__(
        self,
        table_substitution: Dict[table.TableLike, table.Table],
    ):
        self._table_substitution = table_substitution

    def eval_column_val(self, expression: expr.ColumnReference) -> expr.ColumnReference:
        target_table = self._table_substitution.get(expression.table)
        if target_table is None:
            return super().eval_column_val(expression)
        else:
            return target_table[expression.name]


class TableReplacementWithNoneDesugaring(IdentityTransform):
    """Replaces all references to `table` with None."""

    def __init__(self, table):
        self._table = table

    def eval_column_val(  # type: ignore[override]
        self, expression: expr.ColumnReference
    ) -> expr.ColumnExpression:
        if expression.table is self._table:
            return expr.ColumnConstExpression(None)
        else:
            return super().eval_column_val(expression)

    def eval_ix(self, expression: expr.ColumnIxExpression) -> expr.ColumnExpression:
        column_expression = super().eval_column_val(expression._column_expression)
        keys_expression = self.eval_expression(expression._keys_expression)
        return expr.ColumnIxExpression(
            column_expression=column_expression,
            keys_expression=keys_expression,
            optional=expression._optional,
        )

    def eval_require(
        self, expression: expr.RequireExpression
    ) -> expr.RequireExpression:
        val = self.eval_expression(expression._val)
        args = [self.eval_expression(arg) for arg in expression._args]
        for arg in args:
            if isinstance(arg, expr.ColumnConstExpression):
                if arg._val is None:
                    return expr.ColumnConstExpression(None)  # type: ignore
        return expr.RequireExpression(val, *args)


class TableCallbackDesugaring(DesugaringTransform):
    table_like: table.TableLike

    def __init__(self, table_like: table.TableLike):
        from pathway.internals import table

        assert isinstance(table_like, table.TableLike)
        self.table_like = table_like

    @abstractmethod
    def callback(self, *args, **kwargs):
        pass

    def eval_call(self, expression: expr.ColumnCallExpression) -> expr.ColumnReference:
        args: Dict[str, expr.ColumnExpression] = {
            f"arg_{index}": self.eval_expression(arg)
            for index, arg in enumerate(expression._args)
        }

        from pathway.internals import type_interpreter

        method_call_transformer = RowTransformer.method_call_transformer(
            len(args), dtype=type_interpreter.eval_type(expression)
        )
        method_call_input = self.callback(method=expression._col_expr, **args)
        call_result = method_call_transformer(table=method_call_input)
        table = call_result.table
        return table.result


class TableSelectDesugaring(TableCallbackDesugaring):
    table_like: table.Joinable

    def __init__(self, table_like: table.Joinable):
        from pathway.internals import table

        assert isinstance(table_like, table.Joinable)
        super().__init__(table_like)

    def callback(self, *args, **kwargs):
        return self.table_like.select(*args, **kwargs)


class TableReduceDesugaring(TableCallbackDesugaring):
    table_like: groupby.GroupedJoinable

    def __init__(self, table_like: groupby.GroupedJoinable):
        from pathway.internals import groupby

        assert isinstance(table_like, groupby.GroupedJoinable)
        super().__init__(table_like)

    def callback(self, *args, **kwargs):
        return self.table_like.reduce(*args, **kwargs)

    def eval_reducer(
        self, expression: expr.ReducerExpression
    ) -> expr.ReducerExpression:
        select_desugar = TableSelectDesugaring(self.table_like._joinable_to_group)
        args = [select_desugar.eval_expression(arg) for arg in expression._args]
        return expr.ReducerExpression(expression._reducer, *args)


ColExprT = TypeVar("ColExprT", bound=expr.ColumnExpression)


def _desugar_this_arg(
    substitution: Dict[thisclass.ThisMetaclass, table.Joinable],
    expression: ColExprT,
) -> ColExprT:
    return ThisDesugaring(substitution).eval_expression(expression)


def _desugar_this_args(
    substitution: Dict[thisclass.ThisMetaclass, table.Joinable],
    args: Iterable[ColExprT],
) -> Tuple[ColExprT, ...]:
    ret: List[ColExprT] = []
    from pathway.internals import thisclass

    for arg in args:
        if isinstance(arg, thisclass.ThisMetaclass):
            assert issubclass(arg, thisclass.iter_guard)
            evaled_table = arg._eval_substitution(substitution)
            ret.extend(evaled_table)
        else:
            ret.append(_desugar_this_arg(substitution, arg))

    return tuple(ret)


def _desugar_this_kwargs(
    substitution: Dict[thisclass.ThisMetaclass, table.Joinable],
    kwargs: Mapping[str, ColExprT],
) -> Dict[str, ColExprT]:
    from pathway.internals import thisclass

    new_kwargs = {
        name: arg
        for name, arg in kwargs.items()
        if not name.startswith(thisclass.KEY_GUARD)
    }
    for name, arg in kwargs.items():
        if name.startswith(thisclass.KEY_GUARD):
            assert isinstance(arg, thisclass.ThisMetaclass)
            evaled_table = arg._eval_substitution(substitution)
            new_kwargs.update(evaled_table)
    return {
        name: _desugar_this_arg(substitution, arg) for name, arg in new_kwargs.items()
    }


def combine_args_kwargs(
    args: Iterable[expr.ColumnReference | expr.ColumnIxExpression],
    kwargs: Mapping[str, Any],
) -> Dict[str, expr.ColumnExpression]:
    all_args = {}

    def add(name, expression):
        from pathway.internals import table

        assert not isinstance(expression, table.Table)
        if name in all_args:
            raise ValueError(f"Duplicate expression value given for {name}")
        if name == "id":
            raise ValueError("Can't use 'id' as a column name")
        if not isinstance(expression, expr.ColumnExpression):
            expression = expr.ColumnConstExpression(expression)
        all_args[name] = expression

    for expression in args:
        add(expr.smart_name(expression), expression)
    for name, expression in kwargs.items():
        add(name, expression)

    return all_args


class DesugaringContext:
    _substitution: Dict[thisclass.ThisMetaclass, table.Joinable] = {}

    @property
    @abstractmethod
    def _desugaring(self) -> DesugaringTransform:
        pass


@with_optional_kwargs
def desugar(func, **kwargs):
    fn_spec = function_spec(func)
    substitution_param = kwargs.get("substitution", {})

    @wraps(func)
    def wrapper(*args, **kwargs):
        named_args = {**dict(zip(fn_spec.arg_names, args)), **kwargs}
        assert len(named_args) > 0
        first_arg = next(iter(named_args.values()))
        desugaring_context = (
            first_arg if isinstance(first_arg, DesugaringContext) else None
        )

        this_substitution = {}
        if desugaring_context is not None:
            this_substitution.update(desugaring_context._substitution)

        for key, value in substitution_param.items():
            assert isinstance(value, str)
            this_substitution[key] = named_args[value]

        args = _desugar_this_args(this_substitution, args)
        kwargs = _desugar_this_kwargs(this_substitution, kwargs)

        if desugaring_context is not None:
            args = tuple(
                desugaring_context._desugaring.eval_expression(arg) for arg in args
            )
            kwargs = {
                key: desugaring_context._desugaring.eval_expression(value)
                for key, value in kwargs.items()
            }
        return func(*args, **kwargs)

    return wrapper
