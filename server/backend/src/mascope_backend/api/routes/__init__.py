from mascope_backend.api.new.auth.routes import auth_router
from mascope_backend.api.new.cheminfo.routes import cheminfo_router
from mascope_backend.api.new.file.routes import file_router
from mascope_backend.api.new.instrument_configs.routes import (
    instrument_configs_router,
)
from mascope_backend.api.new.instruments import instruments_router
from mascope_backend.api.new.ionization.modes.routes import (
    ionization_mode_router,
)
from mascope_backend.api.new.match.records.routes import match_records_router
from mascope_backend.api.new.params import params_router
from mascope_backend.api.new.peak_assignments.routes import (
    peak_assignments_router,
)
from mascope_backend.api.new.roles.routes import roles_router
from mascope_backend.api.new.temp.routes import temp_router
from mascope_backend.api.new.users.admin.routes import admin_router
from mascope_backend.api.new.users.first_owner.routes import (
    first_owner_router,
)
from mascope_backend.api.new.users.me.routes import me_router
from mascope_backend.api.new.users.owner.routes import owner_router
from mascope_backend.api.new.users.routes import users_router
from mascope_backend.api.new.workspaces.routes import workspaces_router
from mascope_backend.api.routes.attribute_templates.attribute_templates_routes import (
    attribute_templates_router,
)
from mascope_backend.api.routes.calibration.calibration_routes import calibration_router
from mascope_backend.api.routes.dataset.acquisition.routes import (
    acquisition_datasets_router,
)
from mascope_backend.api.routes.dataset.dataset_routes import dataset_router
from mascope_backend.api.routes.ionization_mechanisms.ionization_mechanisms_routes import (
    ionization_mechanisms_router,
)
from mascope_backend.api.routes.match.aggregate.batch.match_aggregate_batch_routes import (
    match_aggregate_batch_router,
)
from mascope_backend.api.routes.match.aggregate.sample.match_aggregate_sample_routes import (
    match_aggregate_sample_router,
)
from mascope_backend.api.routes.match.collections.match_collections_routes import (
    match_collections_router,
)
from mascope_backend.api.routes.match.compounds.match_compounds_routes import (
    match_compounds_router,
)
from mascope_backend.api.routes.match.ions.match_ions_routes import match_ions_router
from mascope_backend.api.routes.match.isotopes.match_isotopes_routes import (
    match_isotopes_router,
)
from mascope_backend.api.routes.match.match_routes import match_router
from mascope_backend.api.routes.match.samples.match_samples_routes import (
    match_samples_router,
)
from mascope_backend.api.routes.match_rating.match_rating_routes import (
    match_rating_router,
)
from mascope_backend.api.routes.sample.batches.sample_batches_routes import (
    sample_batches_router,
)
from mascope_backend.api.routes.sample.files.sample_files_routes import (
    sample_files_router,
    sample_files_upload_router,
)
from mascope_backend.api.routes.sample.items.sample_items_routes import (
    sample_items_router,
)
from mascope_backend.api.routes.samples.samples_routes import samples_router
from mascope_backend.api.routes.target.associations.target_collection_in_sample_batch_routes import (
    target_collection_in_sample_batch_router,
)
from mascope_backend.api.routes.target.associations.target_compound_in_target_collection_routes import (
    target_compound_in_target_collection_router,
)
from mascope_backend.api.routes.target.collections.target_collections_routes import (
    target_collections_router,
)
from mascope_backend.api.routes.target.compounds.target_compounds_routes import (
    target_compounds_router,
)
from mascope_backend.api.routes.target.ions.target_ions_routes import target_ions_router
from mascope_backend.api.routes.target.isotopes.target_isotopes_routes import (
    target_isotopes_router,
)
from mascope_backend.api.routes.visualization.visualization_routes import (
    visualization_router,
)


routers = [
    auth_router,
    me_router,
    users_router,
    first_owner_router,
    admin_router,
    owner_router,
    roles_router,
    workspaces_router,
    params_router,
    dataset_router,
    acquisition_datasets_router,
    sample_batches_router,
    samples_router,
    sample_files_router,
    sample_files_upload_router,
    sample_items_router,
    attribute_templates_router,
    target_collections_router,
    target_collection_in_sample_batch_router,
    target_compounds_router,
    target_compound_in_target_collection_router,
    target_ions_router,
    target_isotopes_router,
    ionization_mechanisms_router,
    ionization_mode_router,
    instruments_router,
    instrument_configs_router,
    calibration_router,
    match_records_router,
    match_router,
    match_aggregate_batch_router,
    match_aggregate_sample_router,
    match_samples_router,
    match_collections_router,
    match_compounds_router,
    match_ions_router,
    match_rating_router,
    match_isotopes_router,
    peak_assignments_router,
    visualization_router,
    temp_router,
    cheminfo_router,
    file_router,
]
