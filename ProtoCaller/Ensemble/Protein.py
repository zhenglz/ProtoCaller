import copy as _copy
import logging as _logging
import os as _os
import re as _re
import warnings as _warnings

from Bio import SeqIO as _SeqIO
import parmed as _pmd

import ProtoCaller as _PC

if _PC.BIOSIMSPACE:
    import BioSimSpace as _BSS

from ProtoCaller.Ensemble import Ligand
import ProtoCaller.IO.PDB as _PDB
import ProtoCaller.IO.GROMACS as _GROMACS
import ProtoCaller.Parametrise as _parametrise
import ProtoCaller.Utils.fileio as _fileio
import ProtoCaller.Utils.pdbconnect as _pdbconnect
import ProtoCaller.Wrappers.charmmguiwrapper as _charmmwrap
if _PC.MODELLER:
    import ProtoCaller.Wrappers.modellerwrapper as _modeller
import ProtoCaller.Wrappers.parmedwrapper as _pmdwrap
import ProtoCaller.Wrappers.pdbfixerwrapper as _pdbfix
import ProtoCaller.Wrappers.pdb2pqrwrapper as _PDB2PQR


class Protein:
    """
    The main class responsible for handling proteins in ProtoCaller.

    Parameters
    ----------
    code : str
        The PDB code of the protein.
    pdb_file : str, optional
        Path to custom PDB file.
    ligands : [str or ProtoCaller.Ensemble.Ligand.Ligand], optional
        Paths to and / or objects of custom ligands.
    ligand_ref : str or ProtoCaller.Ensemble.Ligand.Ligand or None or False
        Initialises ligand_ref from a "resSeq/iCode" (e.g. "400G") or from a Ligand or from a file or automatically
        (None) or enforces no reference ligand (False).
    fasta_file : str, optional
        Path to custom FASTA file.
    complex_template : BioSimSpace.System
        Initialises the complex from an already created complex_template.
    name  optional : str
        Initialises the protein name.
    workdir : str, optional
        Initialises workdir.

    Attributes
    ----------
    workdir : ProtoCaller.Utils.fileio.Dir
        The working directory of the protein.
    name : str
        The protein name. Default value is the PDB code of the protein.
    """
    _counter = 1

    def __init__(self, code=None, pdb_file=None, ligands=None, ligand_ref=None, fasta_file=None, complex_template=None,
                 name=None, workdir=None):
        self.name = name if name is not None else code
        self.workdir = _fileio.Dir(workdir) if workdir else _fileio.Dir(self.name)
        with self.workdir:
            self.code = code
            self.complex_template = complex_template
            if pdb_file:
                self.pdb = pdb_file
            else:
                self.pdb = complex_template
            self.fasta = fasta_file
            self.ligands = ligands
            self.cofactors = []
            # remove ligands from PDB file and non-ligands from SDF files
            self.filter(missing_residues="all", chains="all", waters="all",
                        simple_anions="all", complex_anions="all",
                        simple_cations="all", complex_cations="all",
                        ligands="all", cofactors="all")
            self.ligand_ref = ligand_ref

    @property
    def code(self):
        """str: The PDB code of the protein."""
        return self._code

    @code.setter
    def code(self, input):
        try:
            self._downloader = _pdbconnect.PDBDownloader(input)
            self._code = input
        except:
            self._downloader = None
            self._code = None

    @property
    def complex_template(self):
        """BioSimSpace.System: The prepared system without solvent and ligands."""
        return self._complex_template

    @complex_template.setter
    def complex_template(self, input):
        if not input:
            self._complex_template = None
        elif isinstance(input, _BSS._SireWrappers.System):
            self._complex_template = input
        elif isinstance(input, _BSS._SireWrappers.Molecule):
            self._complex_template = _BSS._SireWrappers.System(input)
        elif isinstance(input, _pmd.Structure):
            if _PC.BIOSIMSPACE:
                tempfiles = ["temp.gro", "temp.top"]
                _pmdwrap.saveFilesFromParmed(input, tempfiles)
                self._complex_template = _BSS.IO.readMolecules(tempfiles)
                for tempfile in tempfiles:
                    _os.remove(tempfile)
            else:
                self._complex_template = input
        else:
            if _PC.BIOSIMSPACE:
                self._complex_template = _BSS.IO.readMolecules(input)
            else:
                self._complex_template = _pmdwrap.openFilesAsParmed(input)

    @property
    def ligands(self):
        """[ProtoCaller.Ensemble.Ligand.Ligand]: Additional ligands in the system."""
        return self._ligands

    @ligands.setter
    def ligands(self, input):
        self._ligands = []
        if input is None and self._downloader:
            input = self._downloader.getLigands()
        if input:
            self._ligands = [Ligand(x, name=_os.path.splitext(_os.path.basename(x))[0], workdir=".", minimise=False)
                             if not isinstance(x, Ligand) else x for x in input]

    @property
    def ligand_ref(self):
        """ProtoCaller.Ensemble.Ligand.Ligand: The reference ligand."""
        return self._ligand_ref

    @ligand_ref.setter
    def ligand_ref(self, input):
        if hasattr(self, "_ligand_ref") and self._ligand_ref is not None:
            raise ValueError("Cannot reassign an already assigned reference ligand. Please create a new class instance")
        elif input is None and len(self.ligands) == 1:
            self._ligand_ref = self.ligands[0]
            self.ligands = []
        elif input is False:
            self._ligand_ref = None
        elif isinstance(input, Ligand):
            self._ligand_ref = input
        elif isinstance(input, str):
            for i, ligand in enumerate(self.ligands):
                _, _, chainID, resSeq = _re.search(r"^([^_\W]+)_([^_\W]+)_([^_\W])_([^_\W]+)", ligand.name).groups()
                chainID_inp, resSeq_inp, iCode_inp = self._residTransform(input)
                if chainID == chainID_inp and resSeq == (str(resSeq_inp) + iCode_inp).strip():
                    self._ligand_ref = ligand
                    del self.ligands[i]
                    return
            raise ValueError("Molecule ID not found: %s" % input)
        else:
            try:
                self._ligand_ref = Ligand(input, name=self.name + "_ref", workdir=".", minimise=False)
            except:
                raise TypeError("Unrecognised type of input. Need either a Ligand or a PDB ID")

    @property
    def name(self):
        """The name of the protein"""
        return self._name

    @name.setter
    def name(self, val):
        if val is None:
            self._name = "protein%d" % self._counter
            Protein._counter += 1
        else:
            self._name = val

    @property
    def pdb(self):
        """str: The absolute path to the PDB file for the protein."""
        with self.workdir:
            if self._pdb is None and self._downloader:
                return self._downloader.getPDB()
            return self._pdb

    @pdb.setter
    def pdb(self, value):
        with self.workdir:
            self._pdb = "{}.pdb".format(self.name)
            if isinstance(value, _BSS._SireWrappers.System):
                _BSS.IO.saveMolecules(self.name, value, "pdb")
            elif isinstance(value, _BSS._SireWrappers.Molecule):
                _BSS.IO.saveMolecules(self.name, _BSS._SireWrappers.System(value), "pdb")
            elif isinstance(value, _pmd.Structure):
                _pmdwrap.saveFilesFromParmed(value, self._pdb)
            else:
                self._pdb = None
                if value is not None:
                    value = _fileio.checkFileExists(value)
                if value:
                    try:
                        try:
                            self._pdb = value
                            self._pdb_obj = _PDB.PDB(self.pdb)
                        except:
                            obj = _pmdwrap.openFilesAsParmed(value)
                            self._pdb = "{}.pdb".format(self.name)
                            _pmdwrap.saveFilesFromParmed(obj, self._pdb)
                            self._pdb_obj = _PDB.PDB(self.pdb)
                        self._checkfasta()
                    except:
                        self._pdb = None

            if not self._pdb:
                self._pdb_obj = _PDB.PDB(self.pdb) if self.pdb else None

    @property
    def pdb_obj(self):
        """ProtoCaller.IO.PDB.PDB: The object corresponding to the PDB file."""
        return self._pdb_obj

    @property
    def fasta(self):
        """str: The absolute path to the FASTA file for the protein."""
        with self.workdir:
            if self._fasta is None and self._downloader:
                return self._downloader.getFASTA()
            return self._fasta

    @fasta.setter
    def fasta(self, value):
        if value is not None:
            value = _fileio.checkFileExists(value)
        self._fasta = value
        if value:
            self._checkfasta()

    def filter(self, missing_residues="middle", chains="all", waters="chain", ligands="chain", cofactors="chain",
               simple_anions="chain", complex_anions="chain", simple_cations="chain", complex_cations="chain",
               include_mols=None, exclude_mols=None):
        """
        Conditionally removes certain molecules from the PDB object and rewrites the PDB file.

        Parameters
        ----------
        missing_residues : str
            One of "all" or "middle". Determines whether to only add missing residues when they are non-terminal.
        chains : str
            One of "all" or an iterable of characters for chains to keep.
        waters : str or None
            One of "all", "chain" (only keep molecules belonging to a chain), "site" (only keep if they are mentioned in
            the PDB SITE directive) or None (no molecules are included).
        ligands : str or None
            One of "all", "chain" (only keep molecules belonging to a chain), "site" (only keep if they are mentioned in
            the PDB SITE directive) or None (no molecules are included).
        cofactors : str or None
            One of "all", "chain" (only keep molecules belonging to a chain), "site" (only keep if they are mentioned in
            the PDB SITE directive) or None (no molecules are included).
        simple_anions : str or None
            One of "all", "chain" (only keep molecules belonging to a chain), "site" (only keep if they are mentioned in
            the PDB SITE directive) or None (no molecules are included).
        complex_anions : str or None
            One of "all", "chain" (only keep molecules belonging to a chain), "site" (only keep if they are mentioned in
            the PDB SITE directive) or None (no molecules are included).
        simple_cations : str or None
            One of "all", "chain" (only keep molecules belonging to a chain), "site" (only keep if they are mentioned in
            the PDB SITE directive) or None (no molecules are included).
        complex_cations : str or None
            One of "all", "chain" (only keep molecules belonging to a chain), "site" (only keep if they are mentioned in
            the PDB SITE directive) or None (no molecules are included).
        include_mols : [str]
            A list of strings which specify molecules that should be included. Overrides
            previous filters.
        exclude_mols : [str]
            A list of strings which specify molecules that should be excluded. Overrides
            previous filters.
        """
        if include_mols is None: include_mols = []
        if exclude_mols is None: exclude_mols = []

        with self.workdir:
            # filter ligands / cofactors
            temp_dict = {"ligand": [], "cofactor": []}
            for molecule in self.ligands + self.cofactors:
                molname = molecule.name
                for param, name in zip([ligands, cofactors], ["ligand", "cofactor"]):
                    # turn the ligand into a pseudo-residue
                    resname, chainID, resSeq_iCode = molname.split("_")[1:4]
                    _, resSeq, iCode = self._residTransform(resSeq_iCode)

                    # filter
                    if _PC.RESIDUETYPE(resname) == name and resSeq_iCode not in exclude_mols:
                        if any([param == "all",
                                param == "chain" and chains == "all",
                                param == "chain" and chainID in chains,
                                resSeq_iCode in include_mols]):
                            temp_dict[name] += [molecule]
                        elif param == "site":
                            for residue in self._pdb_obj.site_residues:
                                if residue.resSeq == resSeq and residue.iCode == iCode:
                                    temp_dict[name] += [molecule]
            self.ligands = temp_dict["ligand"]
            self.cofactors = temp_dict["cofactor"]

            # filter residues / molecules in protein

            # filter by chain
            filter = []
            mask = "type in ['amino_acid', 'amino_acid_modified']"
            if chains != "all":
                mask += "&chainID in %s" % str(list(chains))
            filter += self._pdb_obj.filter(mask)

            # filter missing residues
            if self._pdb_obj.missing_residues:
                chainIDs = sorted({x.chainID for x in self._pdb_obj})
                fastas = self._openfasta(self.fasta)
                if not set(chainIDs).issubset(set(fastas.keys())):
                    raise ValueError("Not all chains are contained in the FASTA sequence")

                if missing_residues == "middle":
                    missing_reslist = self._pdb_obj.totalResidueList()
                    missing_reslist_new = []

                    for chainID in chainIDs:
                        fasta = fastas[chainID]
                        seq = fasta.seq.tomutable()
                        curr_missing = [x for x in missing_reslist if x.chainID == chainID]

                        for i in range(2):
                            curr_missing.reverse()
                            seq.reverse()
                            current_chain = None
                            for j in reversed(range(0, len(curr_missing))):
                                res = curr_missing[j]
                                if type(res) is _PDB.Missing.MissingResidue and current_chain != res.chainID:
                                    del curr_missing[j]
                                    del seq[j]
                                else:
                                    break
                        fasta.seq = seq
                        missing_reslist_new += curr_missing

                    missing_residue_list = [x for x in missing_reslist_new if type(x) == _PDB.MissingResidue]
                    missing_residues_filter = missing_residue_list
                else:
                    missing_residues_filter = self._pdb_obj.missing_residues

                _SeqIO.write([fastas[chainId] for chainId in chainIDs if chains == "all" or chainID in chains],
                             self.fasta, "fasta")
                if chains != "all":
                    missing_residues_filter = [x for x in missing_residues_filter if x.chainID in chains]
                filter += missing_residues_filter

            # filter by waters / anions / cations
            for param, name in zip([waters, simple_anions, complex_anions, simple_cations, complex_cations],
                                   ["water", "simple_anion", "complex_anion", "simple_cation", "complex_cation"]):
                if param == "all" or (param == "chain" and chains == "all"):
                    filter += self._pdb_obj.filter("type=='%s'" % name)
                elif param == "chain":
                    filter += self._pdb_obj.filter("type=='%s'&chainID in %s" % (name, str(list(chains))))
                elif param == "site":
                    if chains == "all":
                        filter_temp = self._pdb_obj.filter("type=='%s'" % name)
                    else:
                        filter_temp = self._pdb_obj.filter("type=='%s'&chainID in %s" % (name, str(list(chains))))
                    filter += [res for res in filter_temp if res in self._pdb_obj.site_residues]

            # include extra molecules / residues
            for include_mol in include_mols:
                chainID, resSeq, iCode = self._residTransform(include_mol)
                filter_str = "chainID=='{}'&resSeq=={}&iCode=='{}'&type not in " \
                             "['ligand', 'cofactor']".format(chainID, resSeq,
                                                             iCode)
                residue = self._pdb_obj.filter(filter_str)
                if not len(residue):
                    _warnings.warn("Could not find residue {}.".format(include_mol))
                filter += residue

            # exclude extra molecules / residues
            excl_filter = []
            for exclude_mol in exclude_mols:
                chainID, resSeq, iCode = self._residTransform(exclude_mol)
                filter_str = "chainID=='{}'&resSeq=={}&iCode=='{}'&type not in " \
                             "['ligand', 'cofactor']".format(chainID, resSeq,
                                                             iCode)
                residue = self._pdb_obj.filter(filter_str)
                if not len(residue):
                    _warnings.warn(
                        "Could not find residue {}.".format(exclude_mol))
                excl_filter += residue

            filter = list(set(filter) - set(excl_filter))
            self._pdb_obj.purgeResidues(filter, "keep")
            self._pdb_obj.writePDB(self.pdb)

    def prepare(self, add_missing_residues="pdbfixer",
                add_missing_atoms="pdb2pqr", protonate_proteins="pdb2pqr",
                protonate_ligands="babel", missing_residues_options=None,
                missing_atom_options=None, protonate_proteins_options=None,
                protonate_ligands_options=None, replace_nonstandard_residues=True, force_add_atoms=False):
        """
        Adds missing residues / atoms to the protein and protonates it and the
        relevant ligands.

        Parameters
        ----------
        add_missing_residues : str or None
            How to add missing residues. One of "modeller", "charmm-gui",
            "pdbfixer" and None (no addition).
        add_missing_atoms : str or None
            How to add missing atoms. One of "modeller", "pdb2pqr", "pdbfixer"
            and None (no addition).
        protonate_proteins : str or None
            How to protonate the protein. One of "pdb2pqr" and
            None (no protonation).
        protonate_ligands : str or None
            How to protonate the related ligands / cofactors. One of "babel"
            and None (no protonation).
        missing_residues_options : dict
            Keyword arguments to pass on to the relevant wrapper responsible
            for the addition of missing protein residues.
        missing_atom_options : dict
            Keyword arguments to pass on to the relevant wrapper responsible
            for the addition of missing heavy atoms.
        protonate_proteins_options : dict
            Keyword arguments to pass on to the relevant wrapper responsible
            for protein protonation.
        protonate_ligands_options : dict
            Keyword arguments to pass on to the relevant wrapper responsible
            for ligand protonation.
        replace_nonstandard_residues : bool
            Whether to replace nonstandard residues with their standard
            equivalents.
        force_add_atoms : bool
            Residues with missing backbone atoms will typically refuse to be modelled by PDB2PQR. If this is False, we
            recast these residues as missing residues before modelling.
        """
        with self.workdir:
            add_missing_residues = add_missing_residues.strip().lower() \
                if add_missing_residues is not None else ""
            add_missing_atoms = add_missing_atoms.strip().lower() \
                if add_missing_atoms is not None else ""
            protonate_proteins = protonate_proteins.strip().lower() \
                if protonate_proteins is not None else ""
            protonate_ligands = protonate_ligands.strip().lower() \
                if protonate_ligands is not None else ""

            if missing_residues_options is None:
                missing_residues_options = {}
            if missing_atom_options is None:
                missing_atom_options = {}
            if protonate_proteins_options is None:
                protonate_proteins_options = {}
            if protonate_ligands_options is None:
                protonate_ligands_options = {}

            # compatibility checks
            if add_missing_atoms == "pdb2pqr" and \
                    protonate_proteins != "pdb2pqr":
                _warnings.warn("Cannot currently run PDB2PQR without "
                               "protonation. This will be fixed in a later "
                               "version. Changing protein protonation method "
                               "to PDB2PQR...")
            if len(self._pdb_obj.missing_residues) and \
                    add_missing_residues == "modeller" and not _PC.MODELLER:
                _warnings.warn("Invalid Modeller license. Switching to CHARMM"
                               "-GUI for the addition of missing residues...")
                add_missing_residues = "charmm-gui"
            if len(self._pdb_obj.missing_atoms) and \
                    add_missing_atoms == "modeller" and not _PC.MODELLER:
                _warnings.warn("Invalid Modeller license. Switching to "
                               "PDB2PQR...")
                add_missing_atoms = "pdb2pqr"

            # convert residues with missing backbone atoms into missing residues
            if not force_add_atoms:
                made_changes = False
                purge_list = []
                for i in reversed(range(len(self._pdb_obj.missing_atoms))):
                    atoms = self._pdb_obj.missing_atoms[i]
                    if any(x for x in atoms if x in ["C", "CA", "N"]):
                        purge_list += [(atoms.resName, atoms.chainID, atoms.resSeq, atoms.iCode)]
                        del self._pdb_obj.missing_atoms[i]
                        made_changes = True
                if made_changes:
                    purge_str = "|".join(["(resName=='{}'&chainID=='{}'&resSeq=={}&iCode=='{}')".format(*x)
                                          for x in purge_list])
                    self._pdb_obj.purgeResidues(self._pdb_obj.filter(purge_str, type="residues"), "discard")
                    self._pdb_obj.missing_residues += [_PDB.MissingResidue(*x) for x in purge_list]
                    _PDB.PDB.sortResidueList(self._pdb_obj.missing_residues)
                    self._pdb_obj.writePDB(self.pdb)

            # convert modified residues to normal ones
            if replace_nonstandard_residues and self._pdb_obj.modified_residues:
                self.pdb = _pdbfix.pdbfixerTransform(self.pdb, True, False, False)

            # add missing residues
            if len(self._pdb_obj.missing_residues):
                kwargs = missing_residues_options
                if add_missing_residues == "modeller":
                    if add_missing_atoms == "modeller":
                        atoms = True
                        kwargs = {**kwargs, **missing_atom_options}
                    else:
                        atoms = False
                    if not self.fasta:
                        raise ValueError("No fasta file supplied.")
                    self.pdb = _modeller.modellerTransform(
                        self.pdb, self.fasta, atoms, self.code, **kwargs)
                elif add_missing_residues == "charmm-gui":
                    self.pdb = _charmmwrap.charmmguiTransform(
                        self.pdb, **kwargs)
                elif add_missing_residues == "pdbfixer":
                    atoms = True if add_missing_atoms == "pdbfixer" else False
                    self.pdb = _pdbfix.pdbfixerTransform(
                        self.pdb, False, True, atoms, **kwargs)
                else:
                    _warnings.warn("Protein has missing residues. Please check"
                                   " your PDB file or choose a valid "
                                   "automation protocol")

            # add missing atoms
            if len(self._pdb_obj.missing_atoms):
                kwargs = missing_atom_options
                if add_missing_atoms == "modeller" and \
                        (not len(self._pdb_obj.missing_residues) or
                         add_missing_residues != "modeller"):
                    _warnings.warn("Cannot currently add missing atoms with "
                                   "Modeller when there are no missing "
                                   "residues. Switching to PDB2PQR...")
                    add_missing_atoms = "pdb2pqr"
                elif add_missing_atoms == "pdbfixer" and \
                         add_missing_residues != "pdbfixer":
                    self.pdb = _pdbfix.pdbfixerTransform(
                        self.pdb, False, False, True, **kwargs)

            # protonate proteins
            if "pdb2pqr" in [add_missing_atoms, protonate_proteins]:
                kwargs = {}
                if add_missing_atoms == "pdb2pqr":
                    kwargs = missing_atom_options
                if protonate_proteins == "pdb2pqr":
                    kwargs = {**kwargs, **protonate_proteins_options}
                self.pdb = _PDB2PQR.pdb2pqrTransform(self.pdb, **kwargs)

            # protonate ligands
            kwargs = protonate_ligands_options
            if protonate_ligands == "babel":
                for ligand in self.ligands + self.cofactors:
                    if not ligand.protonated:
                        ligand.protonate(**kwargs)
                if self.ligand_ref and not self.ligand_ref.protonated:
                    self.ligand_ref.protonate(**kwargs)
            else:
                _warnings.warn("Need to protonate all relevant ligands / "
                               "cofactors before any parametrisation")

    def parametrise(self, params=None, reparametrise=False):
        """
        Parametrises the whole protein system.

        Parameters
        ----------
        params : ProtoCaller.Parametrise.Params
            Force field parameters.
        reparametrise : bool
            Whether to reparametrise an already parametrised complex.
        """
        if self.complex_template is not None and not reparametrise:
            _logging.debug("Protein complex template %s is already parametrised." % self.name)
            return

        if params is None:
            params = _parametrise.Params()

        with self.workdir:
            _logging.info("Parametrising original crystal system...")
            # extract non-protein residues from pdb file and save them as separate pdb files
            hetatm_files, hetatm_types = self._pdb_obj.writeHetatms()
            filter = "type in ['amino_acid', 'amino_acid_modified']"
            non_protein_residues = self._pdb_obj.filter(filter)
            self._pdb_obj.purgeResidues(non_protein_residues, "keep")
            self._pdb_obj.reNumberResidues()
            self._pdb_obj.writePDB(self.pdb)

            # create a merged parmed object with all parametrised molecules and save to top/gro which is then read by
            # BioSimSpace
            # we can't use a direct BioSimSpace object because it throws an error if the file contains single ions
            system = _parametrise.parametriseAndLoadPmd(params=params, filename=self.pdb, molecule_type="protein",
                                                        disulfide_bonds=self._pdb_obj.disulfide_bonds)

            # add ligands to the system
            for ligand in self.ligands:
                ligand.parametrise(params, reparametrise=reparametrise, molecule_type="ligand")
                system += _pmdwrap.openFilesAsParmed(ligand.parametrised_files)

            # add cofactors to the system
            for cofactor in self.cofactors:
                id = _re.search(r"^([^_\W]+)_([^_\W]+)_([^_\W])_([^_\W]+)", cofactor.name).group(2)
                cofactor.parametrise(params, reparametrise=reparametrise, molecule_type="cofactor", id=id)
                system += _pmdwrap.openFilesAsParmed(cofactor.parametrised_files)

            # add all other HETATMS to the system
            for filename, type in zip(hetatm_files, hetatm_types):
                system += _parametrise.parametriseAndLoadPmd(params=params, filename=filename, molecule_type=type)

            _GROMACS.saveAsGromacs("complex_template", system)
            if _PC.BIOSIMSPACE:
                self.complex_template = _BSS.IO.readMolecules(["complex_template.top", "complex_template.gro"])
            else:
                self.complex_template = system

    def _checkfasta(self):
        if hasattr(self, "_fasta") and hasattr(self, "_pdb_obj"):
            seqlen = sum(len(x.seq) for x in _SeqIO.parse(open(self.fasta), 'fasta'))
            reslen = len(self._pdb_obj.totalResidueList())
            if seqlen != reslen:
                _warnings.warn("Length of FASTA sequence ({}) does not match "
                               "the length of PDB sequence ({}). Please check "
                               "your input files.".format(seqlen, reslen))

    @staticmethod
    def _openfasta(fasta):
        """
        Opens a FASTA file downloaded from the PDB as a list of sequences.

        Parameters
        ----------
        fasta : str

        Returns
        -------
        data : dict
        """
        data = {}
        for sequence in _SeqIO.parse(open(fasta), 'fasta'):
            sequence.id = sequence.name = sequence.id.split("|")[0]
            id_str = sequence.id
            chain_str = [x for x in sequence.description.split("|") if "Chain" in x][0]
            chains = chain_str.split(" ")[-1].split(",")
            for chain in chains:
                sequence_copy = _copy.copy(sequence)
                sequence_copy.description = sequence.description.replace(chain_str, f"Chain {chain}")
                id_str_copy = id_str.split("_")[0] + "_" + chain
                sequence_copy.id = sequence_copy.name = id_str_copy
                sequence_copy.description = sequence_copy.description.replace(id_str, id_str_copy)
                data[chain] = sequence_copy
        return data

    @staticmethod
    def _residTransform(id):
        """
        Transforms a string from e.g. "400G" to (400, "G")

        Parameters
        ----------
        id : str

        Returns
        -------
        chainID : str
        resSeq : int
        iCode : str
        """
        id = id.strip()
        if id[0].isalpha():
            chainID = id[0]
            id = id[1:]
        else:
            chainID = "A"

        if id[-1].isalpha():
            iCode = id[-1]
            id = id[:-1]
        else:
            iCode = " "

        return chainID, int(float(id)), iCode
