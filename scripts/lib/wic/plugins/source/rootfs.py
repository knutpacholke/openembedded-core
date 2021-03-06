#
# Copyright (c) 2014, Intel Corporation.
#
# SPDX-License-Identifier: GPL-2.0-only
#
# DESCRIPTION
# This implements the 'rootfs' source plugin class for 'wic'
#
# AUTHORS
# Tom Zanussi <tom.zanussi (at] linux.intel.com>
# Joao Henrique Ferreira de Freitas <joaohf (at] gmail.com>
#

import logging
import os
import shutil
import sys

from oe.path import copyhardlinktree, copytree
from pathlib import Path

from wic import WicError
from wic.pluginbase import SourcePlugin
from wic.misc import get_bitbake_var, exec_native_cmd

logger = logging.getLogger('wic')

class RootfsPlugin(SourcePlugin):
    """
    Populate partition content from a rootfs directory.
    """

    name = 'rootfs'

    @staticmethod
    def __get_rootfs_dir(rootfs_dir):
        if os.path.isdir(rootfs_dir):
            return os.path.realpath(rootfs_dir)

        image_rootfs_dir = get_bitbake_var("IMAGE_ROOTFS", rootfs_dir)
        if not os.path.isdir(image_rootfs_dir):
            raise WicError("No valid artifact IMAGE_ROOTFS from image "
                           "named %s has been found at %s, exiting." %
                           (rootfs_dir, image_rootfs_dir))

        return os.path.realpath(image_rootfs_dir)

    @staticmethod
    def __get_pseudo(native_sysroot, rootfs):
        pseudo = "export PSEUDO_PREFIX=%s/usr;" % native_sysroot
        pseudo += "export PSEUDO_LOCALSTATEDIR=%s;" % os.path.join(rootfs, "../pseudo")
        pseudo += "export PSEUDO_PASSWD=%s;" % rootfs
        pseudo += "export PSEUDO_NOSYMLINKEXP=1;"
        pseudo += "%s " % get_bitbake_var("FAKEROOTCMD")
        return pseudo

    @classmethod
    def do_prepare_partition(cls, part, source_params, cr, cr_workdir,
                             oe_builddir, bootimg_dir, kernel_dir,
                             krootfs_dir, native_sysroot):
        """
        Called to do the actual content population for a partition i.e. it
        'prepares' the partition to be incorporated into the image.
        In this case, prepare content for legacy bios boot partition.
        """
        if part.rootfs_dir is None:
            if not 'ROOTFS_DIR' in krootfs_dir:
                raise WicError("Couldn't find --rootfs-dir, exiting")

            rootfs_dir = krootfs_dir['ROOTFS_DIR']
        else:
            if part.rootfs_dir in krootfs_dir:
                rootfs_dir = krootfs_dir[part.rootfs_dir]
            elif part.rootfs_dir:
                rootfs_dir = part.rootfs_dir
            else:
                raise WicError("Couldn't find --rootfs-dir=%s connection or "
                               "it is not a valid path, exiting" % part.rootfs_dir)

        part.rootfs_dir = cls.__get_rootfs_dir(rootfs_dir)

        new_rootfs = None
        # Handle excluded paths.
        if part.exclude_path or part.include_path or part.embed_rootfs:
            # We need a new rootfs directory we can delete files from. Copy to
            # workdir.
            new_rootfs = os.path.realpath(os.path.join(cr_workdir, "rootfs%d" % part.lineno))

            if os.path.lexists(new_rootfs):
                shutil.rmtree(os.path.join(new_rootfs))
            copyhardlinktree(part.rootfs_dir, new_rootfs)

            if os.path.lexists(os.path.join(new_rootfs, "../pseudo")):
                shutil.rmtree(os.path.join(new_rootfs, "../pseudo"))
            copytree(os.path.join(part.rootfs_dir, "../pseudo"),
                     os.path.join(new_rootfs, "../pseudo"))
            pseudo_cmd = "%s -B -m %s -M %s" % (cls.__get_pseudo(native_sysroot,new_rootfs),
                                                part.rootfs_dir, new_rootfs)
            exec_native_cmd(pseudo_cmd, native_sysroot)

            for path in part.include_path or []:
                copyhardlinktree(path, new_rootfs)

            for embed in part.embed_rootfs or []:
                [embed_rootfs, path] = embed
                #we need to remove the initial / for os.path.join to work
                if os.path.isabs(path):
                    path = path[1:]
                if embed_rootfs in krootfs_dir:
                    embed_rootfs = krootfs_dir[embed_rootfs]
                embed_rootfs = cls.__get_rootfs_dir(embed_rootfs)
                tar_file = os.path.realpath(os.path.join(cr_workdir, "aux.tar"))
                tar_cmd = "%s tar cpf %s -C %s ." % (cls.__get_pseudo(native_sysroot,
                                                     embed_rootfs), tar_file, embed_rootfs)
                exec_native_cmd(tar_cmd, native_sysroot)
                untar_cmd = "%s tar xf %s -C %s ." % (cls.__get_pseudo(native_sysroot, new_rootfs),
                                                      tar_file, os.path.join(new_rootfs, path))
                Path(os.path.join(new_rootfs, path)).mkdir(parents=True, exist_ok=True)
                exec_native_cmd(untar_cmd, native_sysroot,
                                cls.__get_pseudo(native_sysroot, new_rootfs))
                os.remove(tar_file)

            for orig_path in part.exclude_path or []:
                path = orig_path
                if os.path.isabs(path):
                    logger.error("Must be relative: --exclude-path=%s" % orig_path)
                    sys.exit(1)

                full_path = os.path.realpath(os.path.join(new_rootfs, path))

                # Disallow climbing outside of parent directory using '..',
                # because doing so could be quite disastrous (we will delete the
                # directory).
                if not full_path.startswith(new_rootfs):
                    logger.error("'%s' points to a path outside the rootfs" % orig_path)
                    sys.exit(1)

                if path.endswith(os.sep):
                    # Delete content only.
                    for entry in os.listdir(full_path):
                        full_entry = os.path.join(full_path, entry)
                        if os.path.isdir(full_entry) and not os.path.islink(full_entry):
                            shutil.rmtree(full_entry)
                        else:
                            os.remove(full_entry)
                else:
                    # Delete whole directory.
                    shutil.rmtree(full_path)

        part.prepare_rootfs(cr_workdir, oe_builddir,
                            new_rootfs or part.rootfs_dir, native_sysroot)
