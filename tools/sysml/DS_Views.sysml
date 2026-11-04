package DS_Views {
    doc /* Remplacant minimal de la bibliotheque DS_Views de CATIA Magic (vues symboliques), charge
         * uniquement pour valider la sortie CATIA Community (out/catia/) avec le parseur OMG.
         * Ne pas coller dans CATIA : la vraie bibliotheque y est deja. */
    package SymbolicViews {
        view def <gv> GeneralView;
        view def <stv> StateTransitionView;
        view def <afv> ActionFlowView;
    }
}
