"""
Classes for future evaluation.

"""

from functools import partial

from .field import Operand, Data, Array, Field
from .domain import Subdomain
from ..tools.general import OrderedSet, unify_attributes
from ..tools.cache import CachedAttribute, CachedMethod

import logging
logger = logging.getLogger(__name__.split('.')[-1])


class Future(Operand):
    """
    Base class for deferred operations on data.

    Parameters
    ----------
    *args : Operands
        Operands. Number must match class attribute `arity`, if present.
    out : data, optional
        Output data object.  If not specified, a new object will be used.

    Notes
    -----
    Operators are stacked (i.e. provided as arguments to other operators) to
    construct trees that represent compound expressions.  Nodes are evaluated
    by first recursively evaluating their subtrees, and then calling the
    `operate` method.

    """

    arity = None
    __array_priority__ = 100.
    store_last = False

    def __init__(self, *args, out=None):

        # Check arity
        if self.arity is not None:
            if len(args) != self.arity:
                raise ValueError("Wrong number of arguments.")
        # Required attributes
        self.args = list(args)
        self.original_args = list(args)
        self.bases = self._build_bases(*args)
        if any(self.bases):
            self.subdomain = Subdomain.from_bases(self.bases)
        else:
            domain = unify_attributes(args, 'domain', require=False)
            self.subdomain = Subdomain.from_domain(domain)
        self.domain = self.subdomain.domain
        self._grid_layout = self.domain.dist.grid_layout
        self._coeff_layout = self.domain.dist.coeff_layout
        self.out = out
        self.kw = {}
        self.last_id = None
        self.scales = self.subdomain.dealias

    def reset(self):
        """Restore original arguments."""
        self.args = list(self.original_args)

    def __repr__(self):
        repr_args = map(repr, self.args)
        return '{}({})'.format(self.name, ', '.join(repr_args))

    def __str__(self):
        str_args = map(str, self.args)
        return '{}({})'.format(self.name, ', '.join(str_args))

    def atoms(self, *types, include_out=False):
        """"""
        atoms = OrderedSet()
        # Recursively collect atoms
        for arg in self.args:
            if isinstance(arg, (Data, Future)):
                atoms.update(arg.atoms(*types, include_out=include_out))
        # Include output as directed
        if include_out:
            if isinstance(self.out, types):
                atoms.add(self.out)

        return atoms

    def has(self, *atoms):
        hasself = type(self) in atoms
        hasargs = any((a in self.atoms() for a in atoms))
        return hasself or hasargs

    def replace(self, old, new):
        """Replace an object in the expression tree."""
        if self == old:
            return new
        elif self.base == old:
            args = [arg.replace(old, new) for arg in self.args]
            return new(*args, **self.kw)
        else:
            args = [arg.replace(old, new) for arg in self.args]
            return self.base(*args, **self.kw)

    def evaluate(self, id=None, force=True):
        """Recursively evaluate operation."""

        # Check storage
        if self.store_last and (id is not None):
            if id == self.last_id:
                return self.last_out
            else:
                # Clear cache to free output field
                self.last_id = None
                self.last_out = None

        # Recursively attempt evaluation of all operator arguments
        # Track evaluation success with flag
        all_eval = True
        for i, a in enumerate(self.args):
            if isinstance(a, Field):
                a.require_scales(self.subdomain.dealias)
            if isinstance(a, Future):
                a_eval = a.evaluate(id=id, force=force)
                # If evaluation succeeds, substitute result
                if a_eval is not None:
                    self.args[i] = a_eval
                # Otherwise change flag
                else:
                    all_eval = False
        # Return None if any arguments are not evaluable
        if not all_eval:
            return None

        # Check conditions unless forcing evaluation
        if force:
            self.enforce_conditions()
        else:
            # Return None if operator conditions are not satisfied
            if not self.check_conditions():
                return None

        # Allocate output field if necessary
        if self.out:
            out = self.out
        else:
            bases = self.bases
            if any(bases):
                out = self.future_type(bases=bases)
            else:
                out = self.future_type(domain=self.domain)
            #out = self.domain.new_data(self.future_type)
            #out = Field(name=str(self), bases=self.bases)

        # Copy metadata
        out.set_scales(self.subdomain.dealias)

        # Perform operation
        self.operate(out)

        # Reset to free temporary field arguments
        self.reset()

        # Update storage
        if self.store_last and (id is not None):
            self.last_id = id
            self.last_out = out

        return out

    def attempt(self, id=None):
        """Recursively attempt to evaluate operation."""
        return self.evaluate(id=id, force=False)

    def check_conditions(self):
        """Check that all argument fields are in proper layouts."""
        # This method must be implemented in derived classes and should return
        # a boolean indicating whether the operation can be computed without
        # changing the layout of any of the field arguments.
        raise NotImplementedError()

    def operate(self, out):
        """Perform operation."""
        # This method must be implemented in derived classes, take an output
        # field as its only argument, and evaluate the operation into this
        # field without modifying the data of the arguments.
        raise NotImplementedError()

    @CachedMethod(max_size=1)
    def as_ncc_operator(self,*args, **kw):
        ncc = self.evaluate()
        return ncc.as_ncc_operator(*args, name=str(self), **kw)


class FutureArray(Future):
    """Class for deferred operations producing an Array."""
    future_type = Array


class FutureField(Future):
    """Class for deferred operations producing a Field."""
    future_type = Field

    @staticmethod
    def parse(string, namespace, domain):
        """Build FutureField from a string expression."""
        expression = eval(string, namespace)
        return FutureField.cast(expression, domain)

    @staticmethod
    def cast(input, domain):
        """Cast an object to a FutureField."""
        from .operators import FieldCopy
        # Cast to operand
        input = Operand.cast(input, domain)
        # Cast to FutureField
        if isinstance(input, FutureField):
            return input
        else:
            return FieldCopy(input, domain)
