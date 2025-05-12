# GCC does not recognize .ixx as a C++ source extension so
# we have to do this instead.

e = executable('modtest',
    'main.cpp',
    'src0.cxx',
    'src1.cxx',
    'src2.cxx',
    'src3.cxx',
    'src4.cxx',
    'src5.cxx',
    'src6.cxx',
    'src7.cxx',
    'src8.cxx',
    'src9.cxx',
    # The following files actually expose module matchedname
    # primary module interface unit
    'mismatchedname.cxx',
    # partition module interface unit
    'mismatchednamea.cxx',
    # partition module implementation unit
    'mismatchednameb.cxx',
    cpp_args: ['-fmodules-ts'],
    )

test('modtest', e)
