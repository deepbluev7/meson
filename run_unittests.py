#!/usr/bin/env python3
# Copyright 2016-2017 The Meson development team

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import stat
import shlex
import subprocess
import re, json
import tempfile
import unittest, os, sys, shutil, time
from glob import glob
from pathlib import PurePath
import mesonbuild.compilers
import mesonbuild.environment
import mesonbuild.mesonlib
from mesonbuild.mesonlib import is_windows
from mesonbuild.environment import detect_ninja, Environment
from mesonbuild.dependencies import PkgConfigDependency, ExternalProgram

if is_windows():
    exe_suffix = '.exe'
else:
    exe_suffix = ''

def get_soname(fname):
    # HACK, fix to not use shell.
    raw_out = subprocess.check_output(['readelf', '-a', fname],
                                      universal_newlines=True)
    pattern = re.compile('soname: \[(.*?)\]')
    for line in raw_out.split('\n'):
        m = pattern.search(line)
        if m is not None:
            return m.group(1)
    raise RuntimeError('Could not determine soname:\n\n' + raw_out)

def get_fake_options(prefix):
    import argparse
    opts = argparse.Namespace()
    opts.cross_file = None
    opts.prefix = prefix
    return opts

class FakeEnvironment(object):
    def __init__(self):
        self.cross_info = None

    def is_cross_build(self):
        return False

class InternalTests(unittest.TestCase):

    def test_version_number(self):
        searchfunc = mesonbuild.environment.search_version
        self.assertEqual(searchfunc('foobar 1.2.3'), '1.2.3')
        self.assertEqual(searchfunc('1.2.3'), '1.2.3')
        self.assertEqual(searchfunc('foobar 2016.10.28 1.2.3'), '1.2.3')
        self.assertEqual(searchfunc('2016.10.28 1.2.3'), '1.2.3')
        self.assertEqual(searchfunc('foobar 2016.10.128'), 'unknown version')
        self.assertEqual(searchfunc('2016.10.128'), 'unknown version')

    def test_mode_symbolic_to_bits(self):
        modefunc = mesonbuild.mesonlib.FileMode.perms_s_to_bits
        self.assertEqual(modefunc('---------'), 0)
        self.assertEqual(modefunc('r--------'), stat.S_IRUSR)
        self.assertEqual(modefunc('---r-----'), stat.S_IRGRP)
        self.assertEqual(modefunc('------r--'), stat.S_IROTH)
        self.assertEqual(modefunc('-w-------'), stat.S_IWUSR)
        self.assertEqual(modefunc('----w----'), stat.S_IWGRP)
        self.assertEqual(modefunc('-------w-'), stat.S_IWOTH)
        self.assertEqual(modefunc('--x------'), stat.S_IXUSR)
        self.assertEqual(modefunc('-----x---'), stat.S_IXGRP)
        self.assertEqual(modefunc('--------x'), stat.S_IXOTH)
        self.assertEqual(modefunc('--S------'), stat.S_ISUID)
        self.assertEqual(modefunc('-----S---'), stat.S_ISGID)
        self.assertEqual(modefunc('--------T'), stat.S_ISVTX)
        self.assertEqual(modefunc('--s------'), stat.S_ISUID | stat.S_IXUSR)
        self.assertEqual(modefunc('-----s---'), stat.S_ISGID | stat.S_IXGRP)
        self.assertEqual(modefunc('--------t'), stat.S_ISVTX | stat.S_IXOTH)
        self.assertEqual(modefunc('rwx------'), stat.S_IRWXU)
        self.assertEqual(modefunc('---rwx---'), stat.S_IRWXG)
        self.assertEqual(modefunc('------rwx'), stat.S_IRWXO)
        # We could keep listing combinations exhaustively but that seems
        # tedious and pointless. Just test a few more.
        self.assertEqual(modefunc('rwxr-xr-x'),
                         stat.S_IRWXU |
                         stat.S_IRGRP | stat.S_IXGRP |
                         stat.S_IROTH | stat.S_IXOTH)
        self.assertEqual(modefunc('rw-r--r--'),
                         stat.S_IRUSR | stat.S_IWUSR |
                         stat.S_IRGRP |
                         stat.S_IROTH)
        self.assertEqual(modefunc('rwsr-x---'),
                         stat.S_IRWXU | stat.S_ISUID |
                         stat.S_IRGRP | stat.S_IXGRP)

    def test_compiler_args_class(self):
        cargsfunc = mesonbuild.compilers.CompilerArgs
        c = mesonbuild.environment.CCompiler([], 'fake', False)
        # Test that bad initialization fails
        self.assertRaises(TypeError, cargsfunc, [])
        self.assertRaises(TypeError, cargsfunc, [], [])
        self.assertRaises(TypeError, cargsfunc, c, [], [])
        # Test that empty initialization works
        a = cargsfunc(c)
        self.assertEqual(a, [])
        # Test that list initialization works
        a = cargsfunc(['-I.', '-I..'], c)
        self.assertEqual(a, ['-I.', '-I..'])
        # Test that there is no de-dup on initialization
        self.assertEqual(cargsfunc(['-I.', '-I.'], c), ['-I.', '-I.'])

        ## Test that appending works
        a.append('-I..')
        self.assertEqual(a, ['-I..', '-I.'])
        a.append('-O3')
        self.assertEqual(a, ['-I..', '-I.', '-O3'])

        ## Test that in-place addition works
        a += ['-O2', '-O2']
        self.assertEqual(a, ['-I..', '-I.', '-O3', '-O2', '-O2'])
        # Test that removal works
        a.remove('-O2')
        self.assertEqual(a, ['-I..', '-I.', '-O3', '-O2'])
        # Test that de-dup happens on addition
        a += ['-Ifoo', '-Ifoo']
        self.assertEqual(a, ['-Ifoo', '-I..', '-I.', '-O3', '-O2'])

        # .extend() is just +=, so we don't test it

        ## Test that addition works
        # Test that adding a list with just one old arg works and yields the same array
        a = a + ['-Ifoo']
        self.assertEqual(a, ['-Ifoo', '-I..', '-I.', '-O3', '-O2'])
        # Test that adding a list with one arg new and one old works
        a = a + ['-Ifoo', '-Ibaz']
        self.assertEqual(a, ['-Ifoo', '-Ibaz', '-I..', '-I.', '-O3', '-O2'])
        # Test that adding args that must be prepended and appended works
        a = a + ['-Ibar', '-Wall']
        self.assertEqual(a, ['-Ibar', '-Ifoo', '-Ibaz', '-I..', '-I.', '-O3', '-O2', '-Wall'])

        ## Test that reflected addition works
        # Test that adding to a list with just one old arg works and DOES NOT yield the same array
        a = ['-Ifoo'] + a
        self.assertEqual(a, ['-Ibar', '-Ifoo', '-Ibaz', '-I..', '-I.', '-O3', '-O2', '-Wall'])
        # Test that adding to a list with just one new arg that is not pre-pended works
        a = ['-Werror'] + a
        self.assertEqual(a, ['-Ibar', '-Ifoo', '-Ibaz', '-I..', '-I.', '-Werror', '-O3', '-O2', '-Wall'])
        # Test that adding to a list with two new args preserves the order
        a = ['-Ldir', '-Lbah'] + a
        self.assertEqual(a, ['-Ibar', '-Ifoo', '-Ibaz', '-I..', '-I.', '-Ldir', '-Lbah', '-Werror', '-O3', '-O2', '-Wall'])

    def test_commonpath(self):
        from os.path import sep
        commonpath = mesonbuild.mesonlib.commonpath
        self.assertRaises(ValueError, commonpath, [])
        self.assertEqual(commonpath(['/usr', '/usr']), sep + 'usr')
        self.assertEqual(commonpath(['/usr', '/usr/']), sep + 'usr')
        self.assertEqual(commonpath(['/usr', '/usr/bin']), sep + 'usr')
        self.assertEqual(commonpath(['/usr/', '/usr/bin']), sep + 'usr')
        self.assertEqual(commonpath(['/usr/./', '/usr/bin']), sep + 'usr')
        self.assertEqual(commonpath(['/usr/bin', '/usr/bin']), sep + 'usr' + sep + 'bin')
        self.assertEqual(commonpath(['/usr//bin', '/usr/bin']), sep + 'usr' + sep + 'bin')
        self.assertEqual(commonpath(['/usr/./bin', '/usr/bin']), sep + 'usr' + sep + 'bin')
        self.assertEqual(commonpath(['/usr/local', '/usr/lib']), sep + 'usr')
        self.assertEqual(commonpath(['/usr', '/bin']), sep)
        self.assertEqual(commonpath(['/usr', 'bin']), '')
        self.assertEqual(commonpath(['blam', 'bin']), '')
        prefix = '/some/path/to/prefix'
        libdir = '/some/path/to/prefix/libdir'
        self.assertEqual(commonpath([prefix, libdir]), str(PurePath(prefix)))


class BasePlatformTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        src_root = os.path.dirname(__file__)
        src_root = os.path.join(os.getcwd(), src_root)
        self.src_root = src_root
        # In case the directory is inside a symlinked directory, find the real
        # path otherwise we might not find the srcdir from inside the builddir.
        self.builddir = os.path.realpath(tempfile.mkdtemp())
        self.logdir = os.path.join(self.builddir, 'meson-logs')
        self.prefix = '/usr'
        self.libdir = os.path.join(self.prefix, 'lib')
        self.installdir = os.path.join(self.builddir, 'install')
        self.meson_command = [sys.executable, os.path.join(src_root, 'meson.py')]
        self.mconf_command = [sys.executable, os.path.join(src_root, 'mesonconf.py')]
        self.mintro_command = [sys.executable, os.path.join(src_root, 'mesonintrospect.py')]
        self.mtest_command = [sys.executable, os.path.join(src_root, 'mesontest.py'), '-C', self.builddir]
        self.ninja_command = [detect_ninja(), '-C', self.builddir]
        self.common_test_dir = os.path.join(src_root, 'test cases/common')
        self.vala_test_dir = os.path.join(src_root, 'test cases/vala')
        self.framework_test_dir = os.path.join(src_root, 'test cases/frameworks')
        self.unit_test_dir = os.path.join(src_root, 'test cases/unit')
        self.orig_env = os.environ.copy()

    def tearDown(self):
        shutil.rmtree(self.builddir)
        os.environ = self.orig_env
        super().tearDown()

    def _run(self, command):
        output = subprocess.check_output(command, stderr=subprocess.STDOUT,
                                         env=os.environ.copy(),
                                         universal_newlines=True)
        print(output)
        return output

    def init(self, srcdir, extra_args=None, default_args=True):
        if extra_args is None:
            extra_args = []
        args = [srcdir, self.builddir]
        if default_args:
            args += ['--prefix', self.prefix,
                     '--libdir', self.libdir]
        self._run(self.meson_command + args + extra_args)
        self.privatedir = os.path.join(self.builddir, 'meson-private')

    def build(self, extra_args=None):
        if extra_args is None:
            extra_args = []
        self._run(self.ninja_command + extra_args)

    def run_tests(self):
        self._run(self.ninja_command + ['test'])

    def install(self):
        os.environ['DESTDIR'] = self.installdir
        self._run(self.ninja_command + ['install'])

    def uninstall(self):
        self._run(self.ninja_command + ['uninstall'])

    def run_target(self, target):
        output = subprocess.check_output(self.ninja_command + [target],
                                         stderr=subprocess.STDOUT,
                                         universal_newlines=True)
        print(output)
        return output

    def setconf(self, arg, will_build=True):
        # This is needed to increase the difference between build.ninja's
        # timestamp and coredata.dat's timestamp due to a Ninja bug.
        # https://github.com/ninja-build/ninja/issues/371
        if will_build:
            time.sleep(1)
        self._run(self.mconf_command + [arg, self.builddir])

    def wipe(self):
        shutil.rmtree(self.builddir)

    def get_compdb(self):
        with open(os.path.join(self.builddir, 'compile_commands.json')) as ifile:
            return json.load(ifile)

    def get_meson_log(self):
        with open(os.path.join(self.builddir, 'meson-logs', 'meson-log.txt')) as f:
            return f.readlines()

    def get_meson_log_compiler_checks(self):
        '''
        Fetch a list command-lines run by meson for compiler checks.
        Each command-line is returned as a list of arguments.
        '''
        log = self.get_meson_log()
        prefix = 'Command line:'
        cmds = [l[len(prefix):].split() for l in log if l.startswith(prefix)]
        return cmds

    def introspect(self, arg):
        out = subprocess.check_output(self.mintro_command + [arg, self.builddir],
                                      universal_newlines=True)
        return json.loads(out)

    def assertPathEqual(self, path1, path2):
        '''
        Handles a lot of platform-specific quirks related to paths such as
        separator, case-sensitivity, etc.
        '''
        self.assertEqual(PurePath(path1), PurePath(path2))

    def assertPathBasenameEqual(self, path, basename):
        msg = '{!r} does not end with {!r}'.format(path, basename)
        # We cannot use os.path.basename because it returns '' when the path
        # ends with '/' for some silly reason. This is not how the UNIX utility
        # `basename` works.
        path_basename = PurePath(path).parts[-1]
        self.assertEqual(PurePath(path_basename), PurePath(basename), msg)


class AllPlatformTests(BasePlatformTests):
    '''
    Tests that should run on all platforms
    '''
    def test_default_options_prefix(self):
        '''
        Tests that setting a prefix in default_options in project() works.
        Can't be an ordinary test because we pass --prefix to meson there.
        https://github.com/mesonbuild/meson/issues/1349
        '''
        testdir = os.path.join(self.common_test_dir, '94 default options')
        self.init(testdir, default_args=False)
        opts = self.introspect('--buildoptions')
        for opt in opts:
            if opt['name'] == 'prefix':
                prefix = opt['value']
        self.assertEqual(prefix, '/absoluteprefix')

    def test_absolute_prefix_libdir(self):
        '''
        Tests that setting absolute paths for --prefix and --libdir work. Can't
        be an ordinary test because these are set via the command-line.
        https://github.com/mesonbuild/meson/issues/1341
        https://github.com/mesonbuild/meson/issues/1345
        '''
        testdir = os.path.join(self.common_test_dir, '94 default options')
        prefix = '/someabs'
        libdir = 'libdir'
        extra_args = ['--prefix=' + prefix,
                      # This can just be a relative path, but we want to test
                      # that passing this as an absolute path also works
                      '--libdir=' + prefix + '/' + libdir]
        self.init(testdir, extra_args, default_args=False)
        opts = self.introspect('--buildoptions')
        for opt in opts:
            if opt['name'] == 'prefix':
                self.assertEqual(prefix, opt['value'])
            elif opt['name'] == 'libdir':
                self.assertEqual(libdir, opt['value'])

    def test_libdir_must_be_inside_prefix(self):
        '''
        Tests that libdir is forced to be inside prefix no matter how it is set.
        Must be a unit test for obvious reasons.
        '''
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        # libdir being inside prefix is ok
        args = ['--prefix', '/opt', '--libdir', '/opt/lib32']
        self.init(testdir, args)
        self.wipe()
        # libdir not being inside prefix is not ok
        args = ['--prefix', '/usr', '--libdir', '/opt/lib32']
        self.assertRaises(subprocess.CalledProcessError, self.init, testdir, args)
        self.wipe()
        # libdir must be inside prefix even when set via mesonconf
        self.init(testdir)
        self.assertRaises(subprocess.CalledProcessError, self.setconf, '-Dlibdir=/opt', False)

    def test_static_library_overwrite(self):
        '''
        Tests that static libraries are never appended to, always overwritten.
        Has to be a unit test because this involves building a project,
        reconfiguring, and building it again so that `ar` is run twice on the
        same static library.
        https://github.com/mesonbuild/meson/issues/1355
        '''
        testdir = os.path.join(self.common_test_dir, '3 static')
        env = Environment(testdir, self.builddir, self.meson_command,
                          get_fake_options(self.prefix), [])
        cc = env.detect_c_compiler(False)
        static_linker = env.detect_static_linker(cc)
        if not isinstance(static_linker, mesonbuild.compilers.ArLinker):
                raise unittest.SkipTest('static linker is not `ar`')
        # Configure
        self.init(testdir)
        # Get name of static library
        targets = self.introspect('--targets')
        self.assertEqual(len(targets), 1)
        libname = targets[0]['filename']
        # Build and get contents of static library
        self.build()
        before = self._run(['ar', 't', os.path.join(self.builddir, libname)]).split()
        # Filter out non-object-file contents
        before = [f for f in before if f.endswith(('.o', '.obj'))]
        # Static library should contain only one object
        self.assertEqual(len(before), 1, msg=before)
        # Change the source to be built into the static library
        self.setconf('-Dsource=libfile2.c')
        self.build()
        after = self._run(['ar', 't', os.path.join(self.builddir, libname)]).split()
        # Filter out non-object-file contents
        after = [f for f in after if f.endswith(('.o', '.obj'))]
        # Static library should contain only one object
        self.assertEqual(len(after), 1, msg=after)
        # and the object must have changed
        self.assertNotEqual(before, after)

    def test_static_compile_order(self):
        '''
        Test that the order of files in a compiler command-line while compiling
        and linking statically is deterministic. This can't be an ordinary test
        case because we need to inspect the compiler database.
        https://github.com/mesonbuild/meson/pull/951
        '''
        testdir = os.path.join(self.common_test_dir, '5 linkstatic')
        self.init(testdir)
        compdb = self.get_compdb()
        # Rules will get written out in this order
        self.assertTrue(compdb[0]['file'].endswith("libfile.c"))
        self.assertTrue(compdb[1]['file'].endswith("libfile2.c"))
        self.assertTrue(compdb[2]['file'].endswith("libfile3.c"))
        self.assertTrue(compdb[3]['file'].endswith("libfile4.c"))
        # FIXME: We don't have access to the linker command

    def test_run_target_files_path(self):
        '''
        Test that run_targets are run from the correct directory
        https://github.com/mesonbuild/meson/issues/957
        '''
        testdir = os.path.join(self.common_test_dir, '58 run target')
        self.init(testdir)
        self.run_target('check_exists')

    def test_install_introspection(self):
        '''
        Tests that the Meson introspection API exposes install filenames correctly
        https://github.com/mesonbuild/meson/issues/829
        '''
        testdir = os.path.join(self.common_test_dir, '8 install')
        self.init(testdir)
        intro = self.introspect('--targets')
        if intro[0]['type'] == 'executable':
            intro = intro[::-1]
        self.assertPathEqual(intro[0]['install_filename'], '/usr/lib/libstat.a')
        self.assertPathEqual(intro[1]['install_filename'], '/usr/bin/prog' + exe_suffix)

    def test_uninstall(self):
        exename = os.path.join(self.installdir, 'usr/bin/prog' + exe_suffix)
        testdir = os.path.join(self.common_test_dir, '8 install')
        self.init(testdir)
        self.assertFalse(os.path.exists(exename))
        self.install()
        self.assertTrue(os.path.exists(exename))
        self.uninstall()
        self.assertFalse(os.path.exists(exename))

    def test_testsetups(self):
        if not shutil.which('valgrind'):
                raise unittest.SkipTest('Valgrind not installed.')
        testdir = os.path.join(self.unit_test_dir, '2 testsetups')
        self.init(testdir)
        self.build()
        self.run_tests()
        with open(os.path.join(self.logdir, 'testlog.txt')) as f:
            basic_log = f.read()
        self.assertRaises(subprocess.CalledProcessError,
                          self._run, self.mtest_command + ['--setup=valgrind'])
        with open(os.path.join(self.logdir, 'testlog-valgrind.txt')) as f:
            vg_log = f.read()
        self.assertFalse('TEST_ENV is set' in basic_log)
        self.assertFalse('Memcheck' in basic_log)
        self.assertTrue('TEST_ENV is set' in vg_log)
        self.assertTrue('Memcheck' in vg_log)

    def assertFailedTestCount(self, failure_count, command):
        try:
            self._run(command)
            self.assertEqual(0, failure_count, 'Expected %d tests to fail.' % failure_count)
        except subprocess.CalledProcessError as e:
            self.assertEqual(e.returncode, failure_count)

    def test_suite_selection(self):
        testdir = os.path.join(self.unit_test_dir, '4 suite selection')
        self.init(testdir)
        self.build()

        self.assertFailedTestCount(3, self.mtest_command)

        self.assertFailedTestCount(0, self.mtest_command + ['--suite', ':success'])
        self.assertFailedTestCount(3, self.mtest_command + ['--suite', ':fail'])
        self.assertFailedTestCount(3, self.mtest_command + ['--no-suite', ':success'])
        self.assertFailedTestCount(0, self.mtest_command + ['--no-suite', ':fail'])

        self.assertFailedTestCount(1, self.mtest_command + ['--suite', 'mainprj'])
        self.assertFailedTestCount(0, self.mtest_command + ['--suite', 'subprjsucc'])
        self.assertFailedTestCount(1, self.mtest_command + ['--suite', 'subprjfail'])
        self.assertFailedTestCount(1, self.mtest_command + ['--suite', 'subprjmix'])
        self.assertFailedTestCount(2, self.mtest_command + ['--no-suite', 'mainprj'])
        self.assertFailedTestCount(3, self.mtest_command + ['--no-suite', 'subprjsucc'])
        self.assertFailedTestCount(2, self.mtest_command + ['--no-suite', 'subprjfail'])
        self.assertFailedTestCount(2, self.mtest_command + ['--no-suite', 'subprjmix'])

        self.assertFailedTestCount(1, self.mtest_command + ['--suite', 'mainprj:fail'])
        self.assertFailedTestCount(0, self.mtest_command + ['--suite', 'mainprj:success'])
        self.assertFailedTestCount(2, self.mtest_command + ['--no-suite', 'mainprj:fail'])
        self.assertFailedTestCount(3, self.mtest_command + ['--no-suite', 'mainprj:success'])

        self.assertFailedTestCount(1, self.mtest_command + ['--suite', 'subprjfail:fail'])
        self.assertFailedTestCount(0, self.mtest_command + ['--suite', 'subprjfail:success'])
        self.assertFailedTestCount(2, self.mtest_command + ['--no-suite', 'subprjfail:fail'])
        self.assertFailedTestCount(3, self.mtest_command + ['--no-suite', 'subprjfail:success'])

        self.assertFailedTestCount(0, self.mtest_command + ['--suite', 'subprjsucc:fail'])
        self.assertFailedTestCount(0, self.mtest_command + ['--suite', 'subprjsucc:success'])
        self.assertFailedTestCount(3, self.mtest_command + ['--no-suite', 'subprjsucc:fail'])
        self.assertFailedTestCount(3, self.mtest_command + ['--no-suite', 'subprjsucc:success'])

        self.assertFailedTestCount(1, self.mtest_command + ['--suite', 'subprjmix:fail'])
        self.assertFailedTestCount(0, self.mtest_command + ['--suite', 'subprjmix:success'])
        self.assertFailedTestCount(2, self.mtest_command + ['--no-suite', 'subprjmix:fail'])
        self.assertFailedTestCount(3, self.mtest_command + ['--no-suite', 'subprjmix:success'])

        self.assertFailedTestCount(2, self.mtest_command + ['--suite', 'subprjfail', '--suite', 'subprjmix:fail'])
        self.assertFailedTestCount(3, self.mtest_command + ['--suite', 'subprjfail', '--suite', 'subprjmix', '--suite', 'mainprj'])
        self.assertFailedTestCount(2, self.mtest_command + ['--suite', 'subprjfail', '--suite', 'subprjmix', '--suite', 'mainprj', '--no-suite', 'subprjmix:fail'])
        self.assertFailedTestCount(1, self.mtest_command + ['--suite', 'subprjfail', '--suite', 'subprjmix', '--suite', 'mainprj', '--no-suite', 'subprjmix:fail', 'mainprj-failing_test'])

        self.assertFailedTestCount(1, self.mtest_command + ['--no-suite', 'subprjfail:fail', '--no-suite', 'subprjmix:fail'])

    def test_build_by_default(self):
        testdir = os.path.join(self.common_test_dir, '137 build by default')
        self.init(testdir)
        self.build()
        genfile = os.path.join(self.builddir, 'generated.dat')
        exe = os.path.join(self.builddir, 'fooprog' + exe_suffix)
        self.assertTrue(os.path.exists(genfile))
        self.assertFalse(os.path.exists(exe))
        self._run(self.ninja_command + ['fooprog' + exe_suffix])
        self.assertTrue(os.path.exists(exe))

    def test_internal_include_order(self):
        testdir = os.path.join(self.common_test_dir, '138 include order')
        self.init(testdir)
        for cmd in self.get_compdb():
            if cmd['file'].endswith('/main.c'):
                cmd = cmd['command']
                break
        else:
            raise Exception('Could not find main.c command')
        if cmd.endswith('.rsp'):
            # Pretend to build so that the rsp files are generated
            self.build(['-d', 'keeprsp', '-n'])
            # Extract the actual command from the rsp file
            rsp = os.path.join(self.builddir, cmd.split('cl @')[1])
            with open(rsp, 'r', encoding='utf-8') as f:
                cmd = f.read()
        incs = [a for a in shlex.split(cmd) if a.startswith("-I")]
        self.assertEqual(len(incs), 8)
        # target private dir
        self.assertPathEqual(incs[0], "-Isub4/someexe@exe")
        # target build subdir
        self.assertPathEqual(incs[1], "-Isub4")
        # target source subdir
        self.assertPathBasenameEqual(incs[2], 'sub4')
        # include paths added via per-target c_args: ['-I'...]
        self.assertPathBasenameEqual(incs[3], 'sub3')
        # target include_directories: build dir
        self.assertPathEqual(incs[4], "-Isub2")
        # target include_directories: source dir
        self.assertPathBasenameEqual(incs[5], 'sub2')
        # target internal dependency include_directories: build dir
        self.assertPathEqual(incs[6], "-Isub1")
        # target internal dependency include_directories: source dir
        self.assertPathBasenameEqual(incs[7], 'sub1')


class WindowsTests(BasePlatformTests):
    '''
    Tests that should run on Cygwin, MinGW, and MSVC
    '''
    def setUp(self):
        super().setUp()
        self.platform_test_dir = os.path.join(self.src_root, 'test cases/windows')

    def test_find_program(self):
        '''
        Test that Windows-specific edge-cases in find_program are functioning
        correctly. Cannot be an ordinary test because it involves manipulating
        PATH to point to a directory with Python scripts.
        '''
        testdir = os.path.join(self.platform_test_dir, '9 find program')
        # Find `cmd` and `cmd.exe`
        prog1 = ExternalProgram('cmd')
        self.assertTrue(prog1.found(), msg='cmd not found')
        prog2 = ExternalProgram('cmd.exe')
        self.assertTrue(prog2.found(), msg='cmd.exe not found')
        self.assertPathEqual(prog1.get_path(), prog2.get_path())
        # Find cmd with an absolute path that's missing the extension
        cmd_path = prog2.get_path()[:-4]
        prog = ExternalProgram(cmd_path)
        self.assertTrue(prog.found(), msg='{!r} not found'.format(cmd_path))
        # Finding a script with no extension inside a directory works
        prog = ExternalProgram(os.path.join(testdir, 'test-script'))
        self.assertTrue(prog.found(), msg='test-script not found')
        # Finding a script with an extension inside a directory works
        prog = ExternalProgram(os.path.join(testdir, 'test-script-ext.py'))
        self.assertTrue(prog.found(), msg='test-script-ext.py not found')
        # Finding a script in PATH w/o extension works and adds the interpreter
        os.environ['PATH'] += os.pathsep + testdir
        prog = ExternalProgram('test-script-ext')
        self.assertTrue(prog.found(), msg='test-script-ext not found in PATH')
        self.assertPathEqual(prog.get_command()[0], sys.executable)
        self.assertPathBasenameEqual(prog.get_path(), 'test-script-ext.py')
        # Finding a script in PATH with extension works and adds the interpreter
        prog = ExternalProgram('test-script-ext.py')
        self.assertTrue(prog.found(), msg='test-script-ext.py not found in PATH')
        self.assertPathEqual(prog.get_command()[0], sys.executable)
        self.assertPathBasenameEqual(prog.get_path(), 'test-script-ext.py')


class LinuxlikeTests(BasePlatformTests):
    '''
    Tests that should run on Linux and *BSD
    '''
    def test_basic_soname(self):
        '''
        Test that the soname is set correctly for shared libraries. This can't
        be an ordinary test case because we need to run `readelf` and actually
        check the soname.
        https://github.com/mesonbuild/meson/issues/785
        '''
        testdir = os.path.join(self.common_test_dir, '4 shared')
        self.init(testdir)
        self.build()
        lib1 = os.path.join(self.builddir, 'libmylib.so')
        soname = get_soname(lib1)
        self.assertEqual(soname, 'libmylib.so')

    def test_custom_soname(self):
        '''
        Test that the soname is set correctly for shared libraries when
        a custom prefix and/or suffix is used. This can't be an ordinary test
        case because we need to run `readelf` and actually check the soname.
        https://github.com/mesonbuild/meson/issues/785
        '''
        testdir = os.path.join(self.common_test_dir, '27 library versions')
        self.init(testdir)
        self.build()
        lib1 = os.path.join(self.builddir, 'prefixsomelib.suffix')
        soname = get_soname(lib1)
        self.assertEqual(soname, 'prefixsomelib.suffix')

    def test_pic(self):
        '''
        Test that -fPIC is correctly added to static libraries when b_staticpic
        is true and not when it is false. This can't be an ordinary test case
        because we need to inspect the compiler database.
        '''
        testdir = os.path.join(self.common_test_dir, '3 static')
        self.init(testdir)
        compdb = self.get_compdb()
        self.assertIn('-fPIC', compdb[0]['command'])
        self.setconf('-Db_staticpic=false')
        # Regenerate build
        self.build()
        compdb = self.get_compdb()
        self.assertNotIn('-fPIC', compdb[0]['command'])

    def test_pkgconfig_gen(self):
        '''
        Test that generated pkg-config files can be found and have the correct
        version and link args. This can't be an ordinary test case because we
        need to run pkg-config outside of a Meson build file.
        https://github.com/mesonbuild/meson/issues/889
        '''
        testdir = os.path.join(self.common_test_dir, '51 pkgconfig-gen')
        self.init(testdir)
        env = FakeEnvironment()
        kwargs = {'required': True, 'silent': True}
        os.environ['PKG_CONFIG_LIBDIR'] = self.privatedir
        simple_dep = PkgConfigDependency('libfoo', env, kwargs)
        self.assertTrue(simple_dep.found())
        self.assertEqual(simple_dep.get_version(), '1.0')
        self.assertIn('-lfoo', simple_dep.get_link_args())

    def test_vala_c_warnings(self):
        '''
        Test that no warnings are emitted for C code generated by Vala. This
        can't be an ordinary test case because we need to inspect the compiler
        database.
        https://github.com/mesonbuild/meson/issues/864
        '''
        testdir = os.path.join(self.vala_test_dir, '5 target glib')
        self.init(testdir)
        compdb = self.get_compdb()
        vala_command = None
        c_command = None
        for each in compdb:
            if each['file'].endswith('GLib.Thread.c'):
                vala_command = each['command']
            elif each['file'].endswith('retcode.c'):
                c_command = each['command']
            else:
                m = 'Unknown file {!r} in vala_c_warnings test'.format(each['file'])
                raise AssertionError(m)
        self.assertIsNotNone(vala_command)
        self.assertIsNotNone(c_command)
        # -w suppresses all warnings, should be there in Vala but not in C
        self.assertIn("'-w'", vala_command)
        self.assertNotIn("'-w'", c_command)
        # -Wall enables all warnings, should be there in C but not in Vala
        self.assertNotIn("'-Wall'", vala_command)
        self.assertIn("'-Wall'", c_command)
        # -Werror converts warnings to errors, should always be there since it's
        # injected by an unrelated piece of code and the project has werror=true
        self.assertIn("'-Werror'", vala_command)
        self.assertIn("'-Werror'", c_command)

    def test_qt5dependency_qmake_detection(self):
        '''
        Test that qt5 detection with qmake works. This can't be an ordinary
        test case because it involves setting the environment.
        '''
        # Verify that qmake is for Qt5
        if not shutil.which('qmake-qt5'):
            if not shutil.which('qmake'):
                raise unittest.SkipTest('QMake not found')
            # For some inexplicable reason qmake --version gives different
            # results when run from the command line vs invoked by Python.
            # Check for both cases in case this behaviour changes in the future.
            output = subprocess.getoutput(['qmake', '--version'])
            if 'Qt version 5' not in output and 'qt5' not in output:
                raise unittest.SkipTest('Qmake found, but it is not for Qt 5.')
        # Disable pkg-config codepath and force searching with qmake/qmake-qt5
        os.environ['PKG_CONFIG_LIBDIR'] = self.builddir
        os.environ['PKG_CONFIG_PATH'] = self.builddir
        testdir = os.path.join(self.framework_test_dir, '4 qt')
        self.init(testdir)
        # Confirm that the dependency was found with qmake
        msg = 'Qt5 native `qmake-qt5` dependency (modules: Core) found: YES\n'
        msg2 = 'Qt5 native `qmake` dependency (modules: Core) found: YES\n'
        mesonlog = self.get_meson_log()
        self.assertTrue(msg in mesonlog or msg2 in mesonlog)

    def get_soname(self, fname):
        output = subprocess.check_output(['readelf', '-a', fname],
                                         universal_newlines=True)
        for line in output.split('\n'):
            if 'SONAME' in line:
                return line.split('[')[1].split(']')[0]
        raise RuntimeError('Readelf gave no SONAME.')

    def _test_soname_impl(self, libpath, install):
        testdir = os.path.join(self.unit_test_dir, '1 soname')
        self.init(testdir)
        self.build()
        if install:
            self.install()

        # File without aliases set.
        nover = os.path.join(libpath, 'libnover.so')
        self.assertTrue(os.path.exists(nover))
        self.assertFalse(os.path.islink(nover))
        self.assertEqual(self.get_soname(nover), 'libnover.so')
        self.assertEqual(len(glob(nover[:-3] + '*')), 1)

        # File with version set
        verset = os.path.join(libpath, 'libverset.so')
        self.assertTrue(os.path.exists(verset + '.4.5.6'))
        self.assertEqual(os.readlink(verset), 'libverset.so.4')
        self.assertEqual(self.get_soname(verset), 'libverset.so.4')
        self.assertEqual(len(glob(verset[:-3] + '*')), 3)

        # File with soversion set
        soverset = os.path.join(libpath, 'libsoverset.so')
        self.assertTrue(os.path.exists(soverset + '.1.2.3'))
        self.assertEqual(os.readlink(soverset), 'libsoverset.so.1.2.3')
        self.assertEqual(self.get_soname(soverset), 'libsoverset.so.1.2.3')
        self.assertEqual(len(glob(soverset[:-3] + '*')), 2)

        # File with version and soversion set to same values
        settosame = os.path.join(libpath, 'libsettosame.so')
        self.assertTrue(os.path.exists(settosame + '.7.8.9'))
        self.assertEqual(os.readlink(settosame), 'libsettosame.so.7.8.9')
        self.assertEqual(self.get_soname(settosame), 'libsettosame.so.7.8.9')
        self.assertEqual(len(glob(settosame[:-3] + '*')), 2)

        # File with version and soversion set to different values
        bothset = os.path.join(libpath, 'libbothset.so')
        self.assertTrue(os.path.exists(bothset + '.1.2.3'))
        self.assertEqual(os.readlink(bothset), 'libbothset.so.1.2.3')
        self.assertEqual(os.readlink(bothset + '.1.2.3'), 'libbothset.so.4.5.6')
        self.assertEqual(self.get_soname(bothset), 'libbothset.so.1.2.3')
        self.assertEqual(len(glob(bothset[:-3] + '*')), 3)

    def test_soname(self):
        self._test_soname_impl(self.builddir, False)

    def test_installed_soname(self):
        self._test_soname_impl(self.installdir + self.libdir, True)

    def test_compiler_check_flags_order(self):
        '''
        Test that compiler check flags override all other flags. This can't be
        an ordinary test case because it needs the environment to be set.
        '''
        Oflag = '-O3'
        os.environ['CFLAGS'] = os.environ['CXXFLAGS'] = Oflag
        testdir = os.path.join(self.common_test_dir, '43 has function')
        self.init(testdir)
        cmds = self.get_meson_log_compiler_checks()
        for cmd in cmds:
            if cmd[0] == 'ccache':
                cmd = cmd[1:]
            # Verify that -I flags from the `args` kwarg are first
            # This is set in the '43 has function' test case
            self.assertEqual(cmd[1], '-I/tmp')
            # Verify that -O3 set via the environment is overriden by -O0
            Oargs = [arg for arg in cmd if arg.startswith('-O')]
            self.assertEqual(Oargs, [Oflag, '-O0'])

    def test_custom_target_exe_data_deterministic(self):
        testdir = os.path.join(self.common_test_dir, '117 custom target capture')
        self.init(testdir)
        meson_exe_dat1 = glob(os.path.join(self.privatedir, 'meson_exe*.dat'))
        self.wipe()
        self.init(testdir)
        meson_exe_dat2 = glob(os.path.join(self.privatedir, 'meson_exe*.dat'))
        self.assertListEqual(meson_exe_dat1, meson_exe_dat2)

    def _test_stds_impl(self, testdir, compiler, p):
        lang_std = p + '_std'
        # Check that all the listed -std=xxx options for this compiler work
        # just fine when used
        for v in compiler.get_options()[lang_std].choices:
            std_opt = '{}={}'.format(lang_std, v)
            self.init(testdir, ['-D' + std_opt])
            cmd = self.get_compdb()[0]['command']
            if v != 'none':
                cmd_std = "'-std={}'".format(v)
                self.assertIn(cmd_std, cmd)
            try:
                self.build()
            except:
                print('{} was {!r}'.format(lang_std, v))
                raise
            self.wipe()
        # Check that an invalid std option in CFLAGS/CPPFLAGS fails
        # Needed because by default ICC ignores invalid options
        cmd_std = '-std=FAIL'
        env_flags = p.upper() + 'FLAGS'
        os.environ[env_flags] = cmd_std
        self.init(testdir)
        cmd = self.get_compdb()[0]['command']
        qcmd_std = "'{}'".format(cmd_std)
        self.assertIn(qcmd_std, cmd)
        with self.assertRaises(subprocess.CalledProcessError,
                               msg='{} should have failed'.format(qcmd_std)):
            self.build()

    def test_compiler_c_stds(self):
        '''
        Test that C stds specified for this compiler can all be used. Can't be
        an ordinary test because it requires passing options to meson.
        '''
        testdir = os.path.join(self.common_test_dir, '1 trivial')
        env = Environment(testdir, self.builddir, self.meson_command,
                          get_fake_options(self.prefix), [])
        cc = env.detect_c_compiler(False)
        self._test_stds_impl(testdir, cc, 'c')

    def test_compiler_cpp_stds(self):
        '''
        Test that C++ stds specified for this compiler can all be used. Can't
        be an ordinary test because it requires passing options to meson.
        '''
        testdir = os.path.join(self.common_test_dir, '2 cpp')
        env = Environment(testdir, self.builddir, self.meson_command,
                          get_fake_options(self.prefix), [])
        cpp = env.detect_cpp_compiler(False)
        self._test_stds_impl(testdir, cpp, 'cpp')

    def test_installed_modes(self):
        '''
        Test that files installed by these tests have the correct permissions.
        Can't be an ordinary test because our installed_files.txt is very basic.
        '''
        # Test file modes
        testdir = os.path.join(self.common_test_dir, '12 data')
        self.init(testdir)
        self.install()

        f = os.path.join(self.installdir, 'etc', 'etcfile.dat')
        found_mode = stat.filemode(os.stat(f).st_mode)
        want_mode = 'rw------T'
        self.assertEqual(want_mode, found_mode[1:])

        f = os.path.join(self.installdir, 'usr', 'bin', 'runscript.sh')
        statf = os.stat(f)
        found_mode = stat.filemode(statf.st_mode)
        want_mode = 'rwxr-sr-x'
        self.assertEqual(want_mode, found_mode[1:])
        if os.getuid() == 0:
            # The chown failed nonfatally if we're not root
            self.assertEqual(0, statf.st_uid)
            self.assertEqual(0, statf.st_gid)

        f = os.path.join(self.installdir, 'usr', 'share', 'progname',
                         'fileobject_datafile.dat')
        orig = os.path.join(testdir, 'fileobject_datafile.dat')
        statf = os.stat(f)
        statorig = os.stat(orig)
        found_mode = stat.filemode(statf.st_mode)
        orig_mode = stat.filemode(statorig.st_mode)
        self.assertEqual(orig_mode[1:], found_mode[1:])
        self.assertEqual(os.getuid(), statf.st_uid)
        if os.getuid() == 0:
            # The chown failed nonfatally if we're not root
            self.assertEqual(0, statf.st_gid)

        self.wipe()
        # Test directory modes
        testdir = os.path.join(self.common_test_dir, '66 install subdir')
        self.init(testdir)
        self.install()

        f = os.path.join(self.installdir, 'usr', 'share', 'sub1')
        statf = os.stat(f)
        found_mode = stat.filemode(statf.st_mode)
        want_mode = 'rwxr-x--t'
        self.assertEqual(want_mode, found_mode[1:])
        if os.getuid() == 0:
            # The chown failed nonfatally if we're not root
            self.assertEqual(0, statf.st_uid)


class RewriterTests(unittest.TestCase):

    def setUp(self):
        super().setUp()
        src_root = os.path.dirname(__file__)
        self.testroot = os.path.realpath(tempfile.mkdtemp())
        self.rewrite_command = [sys.executable, os.path.join(src_root, 'mesonrewriter.py')]
        self.tmpdir = os.path.realpath(tempfile.mkdtemp())
        self.workdir = os.path.join(self.tmpdir, 'foo')
        self.test_dir = os.path.join(src_root, 'test cases/rewrite')

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def read_contents(self, fname):
        with open(os.path.join(self.workdir, fname)) as f:
            return f.read()

    def check_effectively_same(self, mainfile, truth):
        mf = self.read_contents(mainfile)
        t = self.read_contents(truth)
        # Rewriting is not guaranteed to do a perfect job of
        # maintaining whitespace.
        self.assertEqual(mf.replace(' ', ''), t.replace(' ', ''))

    def prime(self, dirname):
        shutil.copytree(os.path.join(self.test_dir, dirname), self.workdir)

    def test_basic(self):
        self.prime('1 basic')
        subprocess.check_call(self.rewrite_command + ['remove',
                                                      '--target=trivialprog',
                                                      '--filename=notthere.c',
                                                      '--sourcedir', self.workdir],
                              universal_newlines=True)
        self.check_effectively_same('meson.build', 'removed.txt')
        subprocess.check_call(self.rewrite_command + ['add',
                                                      '--target=trivialprog',
                                                      '--filename=notthere.c',
                                                      '--sourcedir', self.workdir],
                              universal_newlines=True)
        self.check_effectively_same('meson.build', 'added.txt')
        subprocess.check_call(self.rewrite_command + ['remove',
                                                      '--target=trivialprog',
                                                      '--filename=notthere.c',
                                                      '--sourcedir', self.workdir],
                              universal_newlines=True)
        self.check_effectively_same('meson.build', 'removed.txt')

    def test_subdir(self):
        self.prime('2 subdirs')
        top = self.read_contents('meson.build')
        s2 = self.read_contents('sub2/meson.build')
        subprocess.check_call(self.rewrite_command + ['remove',
                                                      '--target=something',
                                                      '--filename=second.c',
                                                      '--sourcedir', self.workdir],
                              universal_newlines=True)
        self.check_effectively_same('sub1/meson.build', 'sub1/after.txt')
        self.assertEqual(top, self.read_contents('meson.build'))
        self.assertEqual(s2, self.read_contents('sub2/meson.build'))


if __name__ == '__main__':
    unittest.main(buffer=True)
