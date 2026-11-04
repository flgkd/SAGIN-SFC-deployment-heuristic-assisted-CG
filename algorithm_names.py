"""Canonical scientific algorithm names used by displays and exports."""

from record_compatibility import canonical_algorithm

ALGORITHM_NAMES = {
    "saginfe": "SAGINFE", "dwcg_pure": "DWCG-Pure",
    "dwcg_saginfe": "DWCG-SAGINFE", "dwcg_saginfe_pro": "DWCG-SAGINFE-PRO",
    "dwcg_warm": "Warm", "dwcg_pure_hp": "DWCG-Pure-HP",
    "dwcg_saginfe_pro_no_pfe": "PRO-no-PFE",
    "direct_ilp": "ILP-FullObjective", "direct_ilp_acceptance_cold": "ILP-AdmissionOnly",
}
SHORT_NAMES = dict(ALGORITHM_NAMES, dwcg_pure="Pure", dwcg_saginfe="HFP",
                   dwcg_saginfe_pro="PRO", dwcg_pure_hp="HP")


def algorithm_label(identifier, short=False):
    names = SHORT_NAMES if short else ALGORITHM_NAMES
    base, separator, variant = identifier.partition(":")
    label = names.get(canonical_algorithm(base), base)
    if separator:
        return label + " / " + canonical_algorithm(variant).replace("_", "-")
    return label
