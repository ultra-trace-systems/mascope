"""
The rungs of the chemistry binding ladder, as a vocabulary.

``sample_item.bound_by`` records which rung bound an item, and every question
about routing is a count over that column
(``docs/dev/ingest_routing_and_splitting.md``, section 5.2). A misspelled rung
would not fail. It would be written, and then drop out of every count as
quietly as if those files had never been processed - the one failure a column
read only in aggregate can have. So the names live here once: as a type the
item model validates against, as a check constraint the column carries, and as
the list the learner takes its own from.

Its own module, and a pure one, because three layers need it - the ORM model
for the constraint, the item request models for the type, and the learner -
and anywhere else would make one of them import a layer it should not.
"""

from typing import Literal, get_args


#: Every value ``sample_item.bound_by`` may hold.
#:
#: ``"method"`` covers rungs 2 and 4 at once: a confirmed binding and a
#: learned one are the same evidence read at two strengths, and the state of
#: the binding the item names says which it was - which a name frozen onto the
#: item could not, since a binding can be confirmed after it has routed.
#:
#: Detection (rung 5) joins this list when it routes rather than suggests, and
#: adding it takes a migration: the constraint lives in the schema, and the
#: schema-drift test does not compare check constraints, so it will not remind
#: anyone.
BindingRung = Literal["declared", "explicit", "token", "method"]

#: The same names as a tuple, for the constraint and for membership tests.
BINDING_RUNGS: tuple[str, ...] = get_args(BindingRung)

#: The binding itself, which is what rungs 2 and 4 both are.
BOUND_BY_METHOD = "method"

#: Rungs that may teach a binding, strongest first. Every rung except the
#: binding, which cannot teach itself what it already holds.
LEARNING_SOURCES: tuple[str, ...] = tuple(
    rung for rung in BINDING_RUNGS if rung != BOUND_BY_METHOD
)
