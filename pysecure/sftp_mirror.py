import logging

from datetime import datetime
from os import mkdir, unlink, symlink
from collections import deque
from shutil import rmtree
from time import mktime

from pysecure.config import MAX_MIRROR_LISTING_CHUNK_SIZE
from pysecure.utility import local_recurse


class SftpMirror(object):
    def __init__(self, sftp):
        self.__sftp_session = sftp
        self.__log = logging.getLogger('SftpMirror')

    def mirror_to_local_recursive(self, path_from, path_to, log_files=False):
        """Recursively mirror the contents of "path_from" into "path_to"."""
    
        q = deque([''])
        while q:
            path = q.popleft()
            
            full_from = ('%s/%s' % (path_from, path)) if path else path_from
            full_to = ('%s/%s' % (path_to, path)) if path else path_to

            subdirs = self.__mirror_to_local_no_recursion(full_from, full_to, 
                                                        log_files)
            for subdir in subdirs:
                q.append(('%s/%s' % (path, subdir)) if path else subdir)

    def __get_local_files(self, path):
        self.__log.debug("Checking local files.")

        local_dirs = set()
        def local_dir_cb(parent_path, full_path, filename):
            local_dirs.add(filename)
        
        local_entities = set()
        local_files = set()
        local_attributes = {}
        def local_listing_cb(parent_path, listing):
            for entry in listing:
                (filename, mtime, size, flags) = entry

                entity = (filename, mtime, size, flags[1])
                local_entities.add(entity)
                local_files.add(filename)
                local_attributes[filename] = (datetime.fromtimestamp(mtime), 
                                              flags)

        local_recurse(path, 
                      local_dir_cb, 
                      local_listing_cb, 
                      MAX_MIRROR_LISTING_CHUNK_SIZE, 
                      0)

        self.__log.debug("LOCAL:\n(%d) directories\n(%d) files found." % 
                         (len(local_dirs), len(local_files)))

        return (local_dirs, local_entities, local_files, local_attributes)

    def __get_remote_files(self, path):
        self.__log.debug("Checking remote files.")

        remote_dirs = set()
        def remote_dir_cb(parent_path, full_path, entry):
            remote_dirs.add(entry.name)

        remote_entities = set()
        remote_files = set()
        remote_attributes = {}
        def remote_listing_cb(parent_path, listing):
            for (file_path, entry) in listing:
                entity = (entry.name, entry.modified_time, entry.size, 
                          entry.is_symlink)

                remote_entities.add(entity)
                remote_files.add(entry.name)

                flags = (entry.is_regular, entry.is_symlink, entry.is_special)
                remote_attributes[entry.name] = (entry.modified_time_dt, flags)

        self.__sftp_session.recurse(path,
                                    remote_dir_cb, 
                                    remote_listing_cb, 
                                    MAX_MIRROR_LISTING_CHUNK_SIZE,
                                    0)

        self.__log.debug("REMOTE:\n(%d) directories\n(%d) files found." % 
                         (len(remote_dirs), len(remote_files)))

        return (remote_dirs, remote_entities, remote_files, remote_attributes)

    def __get_deltas(self, from_tuple, to_tuple, log_files=False):
        (to_dirs, to_entities, to_files, to_attributes) = to_tuple
        (from_dirs, from_entities, from_files, from_attributes) = from_tuple
    
        self.__log.debug("Checking deltas.")

        # Now, calculate the differences.

        new_dirs = from_dirs - to_dirs
        
        if log_files is True:
            for new_dir in new_dirs:
                logging.debug("Will CREATE directory: %s" % (new_dir))
        
        deleted_dirs = to_dirs - from_dirs

        if log_files is True:
            for deleted_dir in deleted_dirs:
                logging.debug("Will DELETE directory: %s" % (deleted_dir))

        # Get the files from remote that aren't identical to existing local 
        # entries. These will be copied.
        new_entities = from_entities - to_entities

        if log_files is True:
            for new_entity in new_entities:
                logging.debug("Will CREATE file: %s" % (new_entity[0]))

        # Get the files from local that aren't identical to existing remote
        # entries. These will be deleted.
        deleted_entities = to_entities - from_entities

        if log_files is True:
            for deleted_entity in deleted_entities:
                logging.debug("Will DELETE file: %s" % (deleted_entity[0]))

        self.__log.debug("DELTA:\n(%d) new directories\n(%d) deleted "
                         "directories\n(%d) new local files\n(%d) deleted "
                         "local files" % 
                         (len(new_dirs), len(deleted_dirs), 
                          len(new_entities), len(deleted_entities)))

        return (new_dirs, deleted_dirs, new_entities, deleted_entities)

    def __collect_deltas(self, path_from, path_to, log_files=False):
        from_tuple = self.__get_remote_files(path_from)
        to_tuple = self.__get_local_files(path_to)

        delta_tuple = self.__get_deltas(from_tuple, to_tuple, log_files)

        return (from_tuple, to_tuple, delta_tuple)

    def __fix_deltas_at_target(self, context, ops):
        (from_tuple, path_from, path_to, delta_tuple) = context
        (new_dirs, deleted_dirs, new_entities, deleted_entities) = delta_tuple
        (unlink_, rmtree_, mkdir_, copy_, symlink_) = ops

        self.__log.debug("Removing (%d) directories." % (len(deleted_dirs)))

        # Delete all remote-deleted non-directory entries, regardless of type.
        for (name, mtime, size, is_link) in deleted_entities:
            file_path = ('%s/%s' % (path_to, name))
            self.__log.debug("UPDATE: Removing local file-path: %s" % 
                             (file_path))

            unlink_(file_path)

        # Delete all remote-deleted directories. We do this after the 
        # individual files are created so that, if all of the files from the
        # directory are to be removed, we can show progress for each file 
        # rather than blocking on a tree-delete just to error-out on the 
        # unlink()'s, later.
        for name in deleted_dirs:
            final_path = ('%s/%s' % (path_to, name))
            self.__log.debug("UPDATE: Removing local directory: %s" % 
                             (final_path))

            rmtree_(final_path)

        # Create new directories.
        for name in new_dirs:
            final_path = ('%s/%s' % (path_to, name))
            self.__log.debug("UPDATE: Creating local directory: %s" % 
                             (final_path))

            mkdir_(final_path)

        (from_dirs, from_entities, from_files, from_attributes) = from_tuple

        # Write new/changed files. Handle all but "unknown" file types.
        for (name, mtime, size, is_link) in new_entities:
            attr = from_attributes[name]
            (mtime_dt, (is_regular, is_symlink, is_special)) = attr
            
            filepath_from = ('%s/%s' % (path_from, name))
            filepath_to = ('%s/%s' % (path_to, name))

            if is_regular:
                self.__log.debug("UPDATE: Creating regular local file-path: "
                                 "%s" % (filepath_to))

                copy_(filepath_from, 
                                    filepath_to, 
                                    mtime_dt)

            elif is_symlink:
                linked_to = self.__sftp_session.readlink(filepath_from)

                self.__log.debug("UPDATE: Creating symlink at [%s] to [%s]." % 
                                 (filepath_to, linked_to))
            
                # filepath_to: The physical file.
                # linked_to: The target.
                symlink_(linked_to, filepath_to)

            elif is_special:
                # SSH can't indulge us for devices, etc..
                self.__log.warn("Skipping 'special' file at origin: %s" % 
                                (filepath_from))

        return list(from_dirs)

    def __mirror_to_local_no_recursion(self, path_from, path_to, 
                                     log_files=False):
        """Mirror a directory without descending into directories. Return a 
        list of subdirectory names (do not include full path). We will unlink 
        existing files without determining if they're just going to be 
        rewritten and then truncating them because it is our belief, based on 
        what little we could find, that unlinking is, usually, quicker than 
        truncating.
        """

        # Make sure the destination exists.

        self.__log.debug("Ensuring local target directory exists: %s" % 
                         (path_to))

        try:
            mkdir(path_to)
        except OSError:
            already_exists = True
            self.__log.debug("Local target already exists.")
        else:
            already_exists = False
            self.__log.debug("Local target created.")

        delta_result = self.__collect_deltas(path_from, path_to, log_files)
        (from_tuple, to_tuple, delta_tuple) = delta_result

        context = (from_tuple, path_from, path_to, delta_tuple)
        ops = (unlink, 
               rmtree, 
               mkdir, 
               self.__sftp_session.write_to_local, 
               symlink)

        return self.__fix_deltas_at_target(context, ops)

