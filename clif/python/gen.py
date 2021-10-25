# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Generator helpers.

Produces pieces of generated code.
"""

from clif.python import astutils
from clif.python import postconv
from clif.python import slots

VERSION = '0.3'   # CLIF generated API version. Pure informative.
I = '  '


def TopologicalSortSimple(ideps):
  """Simple topological sort working on sequence of integer indices."""
  # Returns permutation indices (list of integers).
  # Using variable names `cons` for dependent, `prod` for dependency
  # (consumer, producer) to increase readability.
  # cons is implied by the index into ideps.
  # prod is the element of ideps (integer or None).
  # This implies that each cons can only have one or no prod.
  # Example: ideps = [2, None, 1]
  # Read as:
  #   0 depends on 2
  #   1 has no dependency
  #   2 depends on 1
  # Expected output permutation: [1, 2, 0]
  # The output permutation guarantees that prod appears before cons.
  # Recursive implementation, subject to maximum recursion limit
  # (sys.getrecursionlimit(), usually 1000).
  permutation = []
  permutation_set = set()
  def FollowDeps(root, cons):
    """Recursively follows dependencies."""
    if cons in permutation_set:
      return
    prod = ideps[cons]
    if prod is not None:
      if prod < 0:
        raise ValueError(
            'Negative value in ideps: ideps[%s] = %s' % (cons, prod))
      if prod >= len(ideps):
        raise ValueError(
            'Value in ideps exceeds its length: ideps[%s] = %s >= %s'
            % (cons, prod, len(ideps)))
      if prod == cons:
        raise ValueError(
            'Trivial cyclic dependency in ideps: ideps[%s] = %s'
            % (cons, prod))
      if prod == root:
        raise ValueError(
            'Cyclic dependency in ideps: following dependencies from'
            ' %s leads back to %s.' % (root, root))
      FollowDeps(root, prod)
    permutation.append(cons)
    permutation_set.add(cons)
  for cons in range(len(ideps)):
    FollowDeps(cons, cons)
  assert len(permutation) == len(ideps)
  return permutation


def WriteTo(channel, lines):
  for s in lines:
    channel.write(s)
    channel.write('\n')


def Headlines(src_file, hdr_files=(), sys_hdr_files=(), open_ns=None):
  """Generate header comment and #includes.

  Args:
    src_file: str - full name of the source file (C++ header)
    hdr_files: [str] - additional c++ headers to #include "str"
      If the first name is PYTHON, #include <Python.h>.
      If str == PYOBJ, forward declare PyObject.
    sys_hdr_files: set(str) - additional c++ headers to #include <str>
    open_ns: str - emit namespace open_ns if not empty.

  Yields:
    source code lines
  """
  yield '/' * 70
  yield '// This file was automatically generated by PyCLIF.'
  yield '// Version %s' % VERSION
  yield '/' * 70
  if src_file:
    yield '// source: %s' % src_file
  yield ''
  python_h = False
  if hdr_files[:1] == ['PYTHON']:
    python_h = True
    yield '#include <Python.h>'
    del hdr_files[0]
  for h in sys_hdr_files:
    if h:
      yield '#include <%s>' % h
  for h in hdr_files:
    if h == 'PYOBJ' and not python_h:
      yield ''
      yield '// Forward "declare" PyObject (instead of #include <Python.h>)'
      yield 'struct _object; typedef _object PyObject;'
    elif h:
      yield '#include "%s"' % h
  if open_ns:
    yield ''
    yield OpenNs(open_ns)


def OpenNs(namespace):
  namespace = (namespace or 'clif').strip(':')
  return ' '.join('namespace %s {' % ns for ns in namespace.split('::'))


def CloseNs(namespace):
  namespace = (namespace or 'clif').strip(':')
  return '} '*(1+namespace.count('::'))+' // namespace '+namespace


def TypeConverters(type_namespace, types, *gen_cvt_args):
  """Generate type converters for types in type_namespace."""
  type_namespace = type_namespace or 'clif'
  yield ''
  yield OpenNs(type_namespace)
  if type_namespace != 'clif':
    yield 'using namespace ::clif;'
    yield 'using ::clif::Clif_PyObjAs;'
    yield 'using ::clif::Clif_PyObjFrom;'
  for t in types:
    for s in t.GenConverters(*gen_cvt_args):
      yield s
  yield ''
  yield CloseNs(type_namespace)


def _DefLine(pyname, cname, meth, doc):
  if 'KEYWORD' in meth or 'NOARGS' in meth:
    cname = '(PyCFunction)'+cname
  if doc is None:
    doc = 'nullptr'
  else:
    doc = '"%s"' % doc
  return '{"%s", %s, %s, %s}' % (pyname, cname, meth, doc)


def _DefTable(ctype, cname, lines):
  yield 'static %s %s[] = {' % (ctype, cname)
  for p in lines:
    yield I+_DefLine(*p)+','
  yield I+'{}'
  yield '};'


class _MethodDef(object):
  name = 'MethodsStaticAlloc'

  def __call__(self, methods):
    yield ''
    for s in _DefTable('PyMethodDef', self.name, methods):
      yield s

MethodDef = _MethodDef()  # pylint: disable=invalid-name


class _GetSetDef(object):
  # pylint: disable=missing-class-docstring
  name = 'Properties'

  def __call__(self, properties, enable_instance_dict):
    props = properties
    if enable_instance_dict:
      props = [
          ('__dict__',
           'pyclif_instance_dict_get',
           'pyclif_instance_dict_set',
           None)] + props
    for s in _DefTable('PyGetSetDef', 'Properties', props):
      yield s

GetSetDef = _GetSetDef()  # pylint: disable=invalid-name


def _TypesInitInDependencyOrder(types_init, raise_if_reordering=False):
  """Yields type_init items in dependency order: base classes before derived."""
  cppname_indices = {}
  for index, (cppname, _, _, _) in enumerate(types_init):
    cppname_indices[cppname] = index
  assert len(cppname_indices) == len(types_init)
  ideps = []
  for cppname, _, wrapped_base, _ in types_init:
    if wrapped_base is not None and wrapped_base not in cppname_indices:
      # INDIRECT DETECTION. Considering current development plans, this code
      # generator is not worth more effort detecting the issue in a more direct
      # way. This is still far better than crashing with a KeyError, or failing
      # at compile time.
      raise NameError(
          'A .clif file is missing a Python-style `from ... import` for a'
          ' base class declared in another header (go/pyclif#pyimport):'
          ' wrapped_derived=%s, wrapped_base=%s' % (cppname, wrapped_base))
    ideps.append(
        None if wrapped_base is None else
        cppname_indices[wrapped_base])
  permutation = TopologicalSortSimple(ideps)
  if raise_if_reordering:  # For development / debugging.
    if list(sorted(permutation)) != permutation:
      msg = [
          'Derived class appearing before base in .clif file: %s'
          % str(permutation)]
      for cppname, _, wrapped_base, _ in types_init:
        msg.append('    %s -> %s' % (cppname, wrapped_base))
      raise RuntimeError('\n'.join(msg))
  for index in permutation:
    yield types_init[index]


def ReadyFunction(types_init):
  """Generate Ready() function to call PyType_Ready for wrapped types."""
  yield ''
  yield 'bool Ready() {'
  have_modname = False
  pybases = set()
  last_pybase = ''
  for cppname, base, wrapped_base, _ in _TypesInitInDependencyOrder(types_init):
    yield I+'%s =' % cppname
    yield I+'%s::_build_heap_type();' % cppname.rsplit('::', 1)[0]
    if base:
      fq_name, toplevel_fq_name = base
      # |base| is a fully qualified Python name.
      # The caller ensures we have only one Python base per each class.
      if base == last_pybase:
        yield I+'Py_INCREF(base_cls);'
      else:
        type_prefix = '' if pybases else 'PyObject* '
        if toplevel_fq_name:
          yield I+('%sbase_cls = ImportFQName("%s", "%s");' %
                   (type_prefix, fq_name, toplevel_fq_name))
        else:
          yield I+('%sbase_cls = ImportFQName("%s");' %
                   (type_prefix, fq_name))
      if base not in pybases:
        yield I+'if (base_cls == nullptr) return false;'
        yield I+'if (!PyObject_TypeCheck(base_cls, &PyType_Type)) {'
        yield I+I+'Py_DECREF(base_cls);'
        yield I+I+(
            'PyErr_SetString(PyExc_TypeError, "Base class %s is not a '
            'new style class inheriting from object.");' % fq_name)
        yield I+I+'return false;'
        yield I+'}'
      yield I+cppname + '->tp_base = %s(base_cls);' % _Cast('PyTypeObject')
      if base not in pybases:
        yield I+'// Check that base_cls is a *statically* allocated PyType.'
        yield I+'if (%s->tp_base->tp_alloc == PyType_GenericAlloc) {' % cppname
        yield I+I+'Py_DECREF(base_cls);'
        yield I+I+('PyErr_SetString(PyExc_TypeError, "Base class %s is a'
                   ' dynamic (Python defined) class.");' % fq_name)
        yield I+I+'return false;'
        yield I+'}'
        last_pybase = base
        pybases.add(base)
    elif wrapped_base:
      # base is Python wrapper type in a C++ class namespace defined locally.
      yield I+'Py_INCREF(%s);' % wrapped_base
      yield I+'%s->tp_base = %s;' % (cppname, wrapped_base)

    yield I+'if (PyType_Ready(%s) < 0) return false;' % cppname
    if not have_modname:
      yield I+'PyObject *modname = PyUnicode_FromString(ThisModuleName);'
      yield I+'if (modname == nullptr) return false;'
      have_modname = True
    yield I+('PyObject_SetAttrString((PyObject *) %s, "__module__", modname);'
             % cppname)
    yield I+'Py_INCREF(%s);  // For PyModule_AddObject to steal.' % cppname
  yield I+'return true;'
  yield '}'


def InitFunction(doc, meth_ref, init, dict_):
  """Generate a function to create the module and initialize it."""
  yield ''
  yield 'static struct PyModuleDef Module = {'
  yield I+'PyModuleDef_HEAD_INIT,'
  yield I+'ThisModuleName,'
  yield I+'"%s", // module doc' % doc
  yield I+'-1,  // module keeps state in global variables'
  yield I+meth_ref+','
  yield I+'nullptr,  // m_slots a.k.a. m_reload'
  yield I+'nullptr,  // m_traverse'
  yield I+'ClearImportCache  // m_clear'
  yield '};'
  yield ''
  yield 'PyObject* Init() {'
  yield I+'PyObject* module = PyModule_Create(&Module);'
  yield I+'if (!module) return nullptr;'
  init_needs_err = False
  for s in init:
    assert ' return' not in s, 'use "goto err;" to handle errors'
    if ' err;' in s: init_needs_err = True
    yield I+s
  for pair in dict_:
    yield I+'if (PyModule_AddObject(module, "%s", %s) < 0) goto err;' % pair
  yield I+'return module;'
  if init_needs_err or dict_:
    yield 'err:'
    yield I+'Py_DECREF(module);'
    yield I+'return nullptr;'
  yield '}'


def PyModInitFunction(init_name='', modname='', ns=''):
  """Generate extension module init function."""
  assert (init_name or modname) and not (init_name and modname)  # xor
  name = init_name or ('PyInit_' + modname)
  yield ''
  yield 'PyMODINIT_FUNC %s(void) {' % name
  yield I+'if (!%s::Ready()) return nullptr;' % ns
  yield I+'return %s::Init();' % ns
  yield '}'


def WrapperClassDef(name, ctype, cname, is_iter, has_iter, iter_ns,
                    enable_instance_dict):
  """Generate wrapper class."""
  assert not (has_iter and is_iter)
  yield ''
  yield 'struct %s {' % name
  yield I+'PyObject_HEAD'
  if is_iter:
    assert not enable_instance_dict
    yield I+'iterator iter;'
  else:
    yield I+'::clif::Instance<%s> cpp;' % ctype
    if enable_instance_dict:
      yield I+'PyObject* instance_dict = nullptr;'
    yield I+'PyObject* weakrefs = nullptr;'
  yield '};'
  if has_iter:
    yield ''
    yield 'namespace %s {' % iter_ns
    yield 'typedef ::clif::Iterator<%s, %s> iterator;' % (cname, has_iter)
    yield '}'


def VirtualOverriderClass(name, pyname, cname, cfqname, isabstract, idfunc,
                          pcfunc, vfuncs):
  """Generate a derived redirector class."""
  yield ''
  # Unfortunately the multiple-inheritance order here matters, probably caused
  # by one or more improper `reinterpret_cast`s.
  yield 'struct %s : %s, PyObjRef {' % (name, cname)
  yield I+'using %s;' % cfqname
  for f in vfuncs:
    for s in _VirtualFunctionCall(
        idfunc(f.name.cpp_name), f, pyname, isabstract, pcfunc):
      yield s
  yield '};'


def TypeObject(ht_qualname, tracked_slot_groups,
               tp_slots, pyname, ctor, wname, fqclassname,
               abstract, iterator, trivial_dtor, subst_cpp_ptr,
               enable_instance_dict, cpp_has_ext_def_ctor):
  """Generate PyTypeObject methods and table.

  Args:
    ht_qualname: str - e.g. Struct or Outer.Inner
    tracked_slot_groups: dict - from gen.GenSlots() call
    tp_slots: dict - values for PyTypeObject slots
    pyname: str - Python class name
    ctor: str - (WRAPped/DEFault/None) type of generated ctor
    wname: str - C++ wrapper class name
    fqclassname: str - FQ C++ class (being wrapped) name
    abstract: bool - wrapped C++ class is abstract
    iterator: str - C++ iterator object if wrapping an __iter__ class else None
    trivial_dtor: bool - if C++ destructor is trivial, no need to allow threads
    subst_cpp_ptr: str - C++ "replacement" class (being wrapped) if any
    enable_instance_dict: bool - add __dict__ to instance
    cpp_has_ext_def_ctor: bool - if the C++ class has extended ctor

  Yields:
     Source code for PyTypeObject and tp_alloc / tp_init / tp_free methods.
  """
  # NOTE: tracked_slot_groups['tp_slots'] and tp_group are similar but
  #       NOT identical. tp_group has additional customizations.
  if ctor:
    yield ''
    yield '// %s __init__' % pyname
    yield 'static int _ctor(PyObject* self, PyObject* args, PyObject* kw);'
  if not iterator:
    yield ''
    yield '// %s __new__' % pyname
    yield 'static PyObject* _new(PyTypeObject* type, Py_ssize_t nitems);'
    tp_slots['tp_alloc'] = '_new'
    tp_slots['tp_new'] = 'PyType_GenericNew'
  yield ''
  yield '// %s __del__' % pyname
  # Use dtor for dynamic types (derived) to wind down malloc'ed C++ obj, so
  # the C++ dtors are run.
  tp_slots['tp_dealloc'] = '_dtor'
  yield 'static void _dtor(PyObject* self) {'
  if not iterator:
    yield I+'if (%s(self)->weakrefs) {' % _Cast(wname)
    yield I+I+'PyObject_ClearWeakRefs(self);'
    yield I+'}'
  if iterator or not trivial_dtor:
    yield I+'Py_BEGIN_ALLOW_THREADS'
  if iterator:
    yield I+iterator+'.~iterator();'
  else:
    # Using ~Instance() leads to AddressSanitizer: heap-use-after-free.
    yield I+'%s(self)->cpp.Destruct();' % _Cast(wname)
  if iterator or not trivial_dtor:
    yield I+'Py_END_ALLOW_THREADS'
  if not iterator and enable_instance_dict:
    yield I+'Py_CLEAR(%s(self)->instance_dict);' % _Cast(wname)
  yield I+'Py_TYPE(self)->tp_free(self);'
  yield '}'
  if not iterator:
    # Use delete for static types (not derived), allocated with _new.
    tp_slots['tp_free'] = '_del'
    yield ''
    yield 'static void _del(void* self) {'
    yield I+'delete %s(self);' % _Cast(wname)
    yield '}'
  tp_slots['tp_init'] = '_ctor' if ctor else 'Clif_PyType_Inconstructible'
  tp_slots['tp_basicsize'] = 'sizeof(%s)' % wname
  tp_slots['tp_itemsize'] = tp_slots['tp_version_tag'] = '0'
  tp_slots['tp_dictoffset'] = tp_slots['tp_weaklistoffset'] = '0'
  tp_slots['tp_flags'] = ' | '.join(tp_slots['tp_flags'])
  if not tp_slots.get('tp_doc'):
    tp_slots['tp_doc'] = '"CLIF wrapper for %s"' % fqclassname
  wtype = '%s_Type' % wname
  yield ''
  yield 'PyTypeObject* %s = nullptr;' % wtype
  yield ''
  yield 'static PyTypeObject* _build_heap_type() {'
  # http://third_party/pybind11/include/pybind11/detail/class.h?l=571&rcl=276599738
  # was used as a reference for the code generated here.
  yield I+'PyHeapTypeObject *heap_type ='
  yield I+I+I+'(PyHeapTypeObject *) PyType_Type.tp_alloc(&PyType_Type, 0);'
  yield I+'if (!heap_type)'
  yield I+I+'return nullptr;'
  # ht_qualname requires Python >= 3.3 (alwyas true for PyCLIF).
  yield I+'heap_type->ht_qualname = (PyObject *) PyUnicode_FromString('
  yield I+I+I+'"%s");' % ht_qualname
  # Following the approach of pybind11 (ignoring the Python docs).
  yield I+'Py_INCREF(heap_type->ht_qualname);'
  yield I+'heap_type->ht_name = heap_type->ht_qualname;'
  yield I+'PyTypeObject *ty = &heap_type->ht_type;'
  yield I+'ty->tp_as_number = &heap_type->as_number;'
  yield I+'ty->tp_as_sequence = &heap_type->as_sequence;'
  yield I+'ty->tp_as_mapping = &heap_type->as_mapping;'
  yield '#if PY_VERSION_HEX >= 0x03050000'
  yield I+'ty->tp_as_async = &heap_type->as_async;'
  yield '#endif'
  for s in slots.GenTypeSlotsHeaptype(tracked_slot_groups, tp_slots):
    yield s
  if not iterator:
    if enable_instance_dict:
      yield (I+'pyclif_instance_dict_enable(ty, offsetof(%s, instance_dict));'
             % wname)
    yield I+'ty->tp_weaklistoffset = offsetof(wrapper, weakrefs);'
  yield I+'return ty;'
  yield '}'
  if ctor:
    yield ''
    yield 'static int _ctor(PyObject* self, PyObject* args, PyObject* kw) {'
    if abstract:
      yield I+'if (Py_TYPE(self) == %s) {' % wtype
      yield I+I+'return Clif_PyType_Inconstructible(self, args, kw);'
      yield I+'}'
    cpp = '%s(self)->cpp' % _Cast(wname)
    if ctor == 'DEF':
      # Skip __init__ if it's a METH_NOARGS.
      yield I+('if ((args && PyTuple_GET_SIZE(args) != 0) ||'
               ' (kw && PyDict_Size(kw) != 0)) {')
      yield I+I+('PyErr_SetString(PyExc_TypeError, "%s takes no arguments");' %
                 pyname)
      yield I+I+'return -1;'
      yield I+'}'
      # We have been lucky so far because NULL initialization of clif::Instance
      # object is equivalent to constructing it with the default constructor.
      # (NULL initialization happens in PyType_GenericAlloc).
      # We don't have a place to call placement new. __init__ (and so _ctor) can
      # be called many times and we have no way to ensure the previous object is
      # destructed properly (it may be NULL or new initialized).
      yield I+'%s = ::clif::MakeShared<%s>();' % (cpp,
                                                  subst_cpp_ptr or fqclassname)
      if subst_cpp_ptr:
        yield I+'%s->::clif::PyObjRef::Init(self);' % cpp
      yield I+'return 0;'
    else:  # ctor is WRAP (holds 'wrapper name')
      if cpp_has_ext_def_ctor:
        yield I+('if ((args && PyTuple_GET_SIZE(args) != 0) ||'
                 ' (kw && PyDict_Size(kw) != 0)) {')
        yield I+I+(
            'PyErr_SetString(PyExc_TypeError, "%s takes no arguments");' %
            pyname)
        yield I+I+'return -1;'
        yield I+'}'
        yield I+'PyObject* init = %s(self);' % ctor
      else:
        yield I+'PyObject* init = %s(self, args, kw);' % ctor
      if subst_cpp_ptr:
        yield I+'if (!init) return -1;'
        yield I+'Py_DECREF(init);'
        yield I+'%s->::clif::PyObjRef::Init(self);' % cpp
        yield I+'return 0;'
      else:
        yield I+'Py_XDECREF(init);'
        yield I+'return init? 0: -1;'
    yield '}'
  if not iterator:
    yield ''
    yield 'static PyObject* _new(PyTypeObject* type, Py_ssize_t nitems) {'
    yield I+'DCHECK(nitems == 0);'
    yield I+'%s* wobj = new %s;' % (wname, wname)
    if enable_instance_dict:
      yield I+'wobj->instance_dict = nullptr;'
    yield I+'PyObject* self = %s(wobj);' % _Cast()
    yield I+'return PyObject_Init(self, %s);' % wtype
    yield '}'


def _CreateInputParameter(func_name, ast_param, arg, args):
  """Returns tuple of (bool, str) and appends to args."""
  # First return value is bool check_nullptr.
  # Second return value is a string to create C++ stack var named arg.
  # Sideeffect: args += arg getter.
  ptype = ast_param.type
  ctype = ptype.cpp_type
  smartptr = (ctype.startswith('::std::unique_ptr') or
              ctype.startswith('::std::shared_ptr'))
  # std::function special case
  if not ctype:
    assert ptype.callable, 'Non-callable param has empty cpp_type'
    if len(ptype.callable.returns) > 1:
      raise ValueError('Callbacks may not have any output parameters, '
                       '%s param %s has %d' % (func_name, ast_param.name.native,
                                               len(ptype.callable.returns)-1))
    args.append('std::move(%s)' % arg)
    return (
        False,
        'std::function<%s> %s;' % (
            astutils.StdFuncParamStr(ptype.callable), arg))
  # T*
  if ptype.cpp_raw_pointer:
    if ptype.cpp_toptr_conversion:
      args.append(arg)
      return (False, '%s %s;' % (ctype, arg))
    t = ctype[:-1]
    if ctype.endswith('*'):
      if ptype.cpp_abstract:
        if ptype.cpp_touniqptr_conversion:
          args.append(arg+'.get()')
          return (False, '::std::unique_ptr<%s> %s;' % (t, arg))
      elif ptype.cpp_has_public_dtor:
        # Create a copy on stack and pass its address.
        if ptype.cpp_has_def_ctor:
          args.append('&'+arg)
          return (False, '%s %s;' % (t, arg))
        else:
          args.append('&%s.value()' % arg)
          return (False, '::absl::optional<%s> %s;' % (t, arg))
    raise TypeError("Can't convert %s to %s" % (ptype.lang_type, ctype))
  if (smartptr or ptype.cpp_abstract) and not ptype.cpp_touniqptr_conversion:
    raise TypeError('Can\'t create "%s" variable (C++ type %s) in function %s'
                    ', no valid conversion defined'
                    % (ast_param.name.native, ctype, func_name))
  # unique_ptr<T>, shared_ptr<T>
  if smartptr:
    args.append('std::move(%s)' % arg)
    return (False, '%s %s;' % (ctype, arg))
  # T, [const] T&
  if ptype.cpp_toptr_conversion:
    args.append('*'+arg)
    return (True, '%s* %s;' % (ctype, arg))
  if ptype.cpp_abstract:  # for AbstractType &
    args.append('*'+arg)
    return (False, 'std::unique_ptr<%s> %s;' % (ctype, arg))
  # Create a copy on stack (even fot T&, most cases should have to_T* conv).
  if ptype.cpp_has_def_ctor:
    args.append('std::move(%s)' % arg)
    return (False, '%s %s;' % (ctype, arg))
  else:
    args.append(arg+'.value()')
    return (False, '::absl::optional<%s> %s;' % (ctype, arg))


def FunctionCall(pyname, wrapper, doc, catch, call, postcall_init,
                 typepostconversion, func_ast, lineno, prepend_self=None):
  """Generate PyCFunction wrapper from AST.FuncDecl func_ast.

  Args:
    pyname: str - Python function name (may be special: ends with @)
    wrapper: str - generated function name
    doc: str - C++ signature
    catch: bool - catch C++ exceptions
    call: str | [str] - C++ command(s) to call the wrapped function
      (without "(params);" part).
    postcall_init: str - C++ command; to (re)set ret0.
    typepostconversion: dict(pytype, index) to convert to pytype
    func_ast: AST.FuncDecl protobuf
    lineno: int - .clif line number where func_ast defined
    prepend_self: AST.Param - Use self as 1st parameter.

  Yields:
     Source code for wrapped function.

  Raises:
    ValueError: for non-supported default arguments
  """
  ctxmgr = pyname.endswith('@')
  if ctxmgr:
    ctxmgr = pyname
    assert ctxmgr in ('__enter__@', '__exit__@'), (
        'Invalid context manager name ' + pyname)
    pyname = pyname.rstrip('@')
  nret = len(func_ast.returns)
  return_type = astutils.FuncReturnType(func_ast)  # Can't use cpp_exact_type.
  # return_type mangled to FQN and drop &, sadly it also drop const.
  void_return_type = 'void' == return_type
  # Has extra func parameters for output values.
  xouts = nret > (0 if void_return_type else 1)
  params = []  # C++ parameter names.
  nargs = len(func_ast.params)
  is_ternaryfunc_slot = pyname == '__call__'
  yield ''
  if func_ast.classmethod:
    yield '// @classmethod ' + doc
    arg0 = 'cls'  # Extra protection that generated code does not use 'self'.
  else:
    yield '// ' + doc
    arg0 = 'self'
  needs_kw = nargs or is_ternaryfunc_slot
  yield 'static PyObject* %s(PyObject* %s%s) {' % (
      wrapper, arg0, ', PyObject* args, PyObject* kw' if needs_kw else '')
  if is_ternaryfunc_slot and not nargs:
    yield I+('if (!ensure_no_args_and_kw_args("%s", args, kw)) return nullptr;'
             % pyname)
  if prepend_self:
    unused_check_nullptr, out = _CreateInputParameter(
        pyname+' line %d' % lineno, prepend_self, 'arg0', params)
    yield I+out
    yield I+'if (!Clif_PyObjAs(self, &arg0)) return nullptr;'
  minargs = sum(1 for p in func_ast.params if not p.default_value)
  if nargs:
    yield I+'PyObject* a[%d]%s;' % (nargs, '' if minargs == nargs else '{}')
    yield I+'const char* names[] = {'
    for p in func_ast.params:
      yield I+I+I+'"%s",' % p.name.native
    yield I+I+I+'nullptr'
    yield I+'};'
    yield I+('if (!PyArg_ParseTupleAndKeywords(args, kw, "%s:%s", '
             'const_cast<char**>(names), %s)) '
             'return nullptr;' % ('O'*nargs if minargs == nargs else
                                  'O'*minargs+'|'+'O'*(nargs-minargs), pyname,
                                  ', '.join('&a[%d]'%i for i in range(nargs))))
    if minargs < nargs and not xouts:
      yield I+'int nargs;  // Find how many args actually passed in.'
      yield I+'for (nargs = %d; nargs > %d; --nargs) {' % (nargs, minargs)
      yield I+I+'if (a[nargs-1] != nullptr) break;'
      yield I+'}'
    # Convert input parameters from Python.
    for i, p in enumerate(func_ast.params):
      n = i+1
      arg = 'arg%d' % n
      check_nullptr, out = _CreateInputParameter(
          pyname+' line %d' % lineno, p, arg, params)
      yield I+out
      return_arg_err = (
          'return ArgError("{func_name}", names[{i}], "{ctype}", a[{i}]);'
      ).format(i=i, func_name=pyname, ctype=astutils.Type(p))
      cvt = ('if (!Clif_PyObjAs(a[{i}], &{cvar}{postconv})) {return_arg_err}'
            ).format(i=i, cvar=arg, return_arg_err=return_arg_err,
                     # Add post conversion parameter for std::function.
                     postconv='' if p.type.cpp_type else ', {%s}' % ', '.join(
                         postconv.Initializer(t.type, typepostconversion)
                         for t in p.type.callable.params))
      def YieldCheckNullptr(ii):
        # pylint: disable=cell-var-from-loop
        if check_nullptr:
          yield ii+'if (%s == nullptr) {' % arg
          yield ii+I+return_arg_err
          yield ii+'}'
      if i < minargs:
        # Non-default parameter.
        yield I+cvt
        for s in YieldCheckNullptr(I):
          yield s
      else:
        if xouts:
          _I = ''  # pylint: disable=invalid-name
        else:
          _I = I   # pylint: disable=invalid-name
          yield I+'if (nargs > %d) {' % i
        # Check if we're passed kw args, skipping some default C++ args.
        # In this case we must substitute missed default args with default_value
        if (p.default_value == 'default'   # Matcher could not find the default.
            or 'inf' in p.default_value):  # W/A for b/29437257
          if xouts:
            raise ValueError("Can't supply the default for C++ function"
                             ' argument. Drop =default in def %s(%s).'
                             % (pyname, p.name.native))
          if n < nargs:
            yield I+I+('if (!a[{i}]) return DefaultArgMissedError('
                       '"{}", names[{i}]);'.format(pyname, i=i))
          yield I+I+cvt
          for s in YieldCheckNullptr(I+I):
            yield s
        elif (p.default_value and
              params[-1].startswith('&') and p.type.cpp_raw_pointer):
          # Special case for a pointer to an integral type param (like int*).
          raise ValueError('A default for integral type pointer argument is '
                           ' not supported. Drop =default in def %s(%s).'
                           % (pyname, p.name.native))
        else:
          # C-cast takes care of the case where |arg| is an enum value, while
          # the matcher would return an integral literal. Using static_cast
          # would be ideal, but its argument should be an expression, which a
          # struct value like {1, 2, 3} is not.
          yield _I+I+'if (!a[%d]) %s = (%s)%s;' % (i, arg, astutils.Type(p),
                                                   p.default_value)
          yield _I+I+'else '+cvt
          for s in YieldCheckNullptr(_I+I):
            yield s
        if not xouts:
          yield I+'}'
  # Create input parameters for extra return values.
  for n, p in enumerate(func_ast.returns):
    if n or void_return_type:
      yield I+'%s ret%d{};' % (astutils.Type(p), n)
      params.append('&ret%d' % n)
  yield I+'// Call actual C++ method.'
  if isinstance(call, list):
    for s in call[:-1]:
      yield I+s
    call = call[-1]
  if not func_ast.py_keep_gil:
    if nargs:
      yield I+'Py_INCREF(args);'
      yield I+'Py_XINCREF(kw);'
    yield I+'PyThreadState* _save;'
    yield I+'Py_UNBLOCK_THREADS'
  optional_ret0 = False
  convert_ref_to_ptr = False
  if (minargs < nargs or catch) and not void_return_type:
    if catch and return_type.rstrip().endswith('&'):
      convert_ref_to_ptr = True
      idx = return_type.rindex('&')
      return_type = return_type[:idx] + '*'
    if func_ast.returns[0].type.cpp_has_def_ctor:
      yield I+return_type+' ret0;'
    else:
      # Using optional<> requires T be have T(x) and T::op=(x) available.
      # While we need only t=x, implementing it will be a pain we skip for now.
      yield I+'::absl::optional<%s> ret0;' % return_type
      optional_ret0 = True
  if catch:
    for s in _GenExceptionTry():
      yield s
  if minargs < nargs and not xouts:
    if not void_return_type:
      call = 'ret0 = '+call
    yield I+'switch (nargs) {'
    for n in range(minargs, nargs+1):
      yield I+'case %d:' % n
      if func_ast.is_extend_method and func_ast.constructor:
        call_with_params = call % (func_ast.name.cpp_name,
                                   astutils.TupleStr(params[:n]))
      else:
        num_params = n
        # extended methods need to include `self` as the first parameter, but
        # extended constructors do not.
        if func_ast.is_extend_method:
          num_params += 1
        call_with_params = call + astutils.TupleStr(params[:num_params])
      yield I+I+'%s; break;' % call_with_params
    yield I+'}'
  else:
    if func_ast.is_extend_method and func_ast.constructor:
      call = call % (func_ast.name.cpp_name, astutils.TupleStr(params))
    else:
      call += astutils.TupleStr(params)
    _I = I if catch else ''  # pylint: disable=invalid-name
    if void_return_type:
      yield _I+I+call+';'
    elif catch:
      if convert_ref_to_ptr:
        yield _I+I+'ret0 = &'+call+';'
      else:
        yield _I+I+'ret0 = '+call+';'
    else:
      yield _I+I+return_type+' ret0 = '+call+';'
  if catch:
    for s in _GenExceptionCatch():
      yield s
  if postcall_init:
    if void_return_type:
      yield I+postcall_init
    else:
      yield I+'ret0'+postcall_init
  if not func_ast.py_keep_gil:
    yield I+'Py_BLOCK_THREADS'
    if nargs:
      yield I+'Py_DECREF(args);'
      yield I+'Py_XDECREF(kw);'
  if catch:
    for s in _GenExceptionRaise():
      yield s
  if func_ast.postproc == '->self':
    func_ast.postproc = ''
    return_self = True
    assert nret == 0, '-> self must have no other output parameters'
  else:
    return_self = False
  ret = '*ret' if convert_ref_to_ptr else 'ret'
  # If ctxmgr, force return self on enter, None on exit.
  if nret > 1 or (func_ast.postproc or ctxmgr) and nret:
    yield I+'// Convert return values to Python.'
    yield I+'PyObject* p, * result_tuple = PyTuple_New(%d);' % nret
    yield I+'if (result_tuple == nullptr) return nullptr;'
    for i in range(nret):
      yield I+'if ((p=Clif_PyObjFrom(std::move(%s%d), %s)) == nullptr) {' % (
          ret, i,
          postconv.Initializer(
              func_ast.returns[i].type,
              typepostconversion,
              marked_non_raising=func_ast.marked_non_raising))
      yield I+I+'Py_DECREF(result_tuple);'
      yield I+I+'return nullptr;'
      yield I+'}'
      yield I+'PyTuple_SET_ITEM(result_tuple, %d, p);' % i
    if func_ast.postproc:
      yield I+'PyObject* pyproc = ImportFQName("%s");' % func_ast.postproc
      yield I+'if (pyproc == nullptr) {'
      yield I+I+'Py_DECREF(result_tuple);'
      yield I+I+'return nullptr;'
      yield I+'}'
      yield I+'p = PyObject_CallObject(pyproc, result_tuple);'
      yield I+'Py_DECREF(pyproc);'
      yield I+'Py_CLEAR(result_tuple);'
      if ctxmgr:
        yield I+'if (p == nullptr) return nullptr;'
        yield I+'Py_DECREF(p);  // Not needed by the context manager.'
      else:
        yield I+'result_tuple = p;'
    if ctxmgr == '__enter__@':
      yield I+'Py_XDECREF(result_tuple);'
      yield I+'Py_INCREF(self);'
      yield I+'return self;'
    elif ctxmgr == '__exit__@':
      yield I+'Py_XDECREF(result_tuple);'
      yield I+'Py_RETURN_NONE;'
    else:
      yield I+'return result_tuple;'
  elif nret:
    yield I+'return Clif_PyObjFrom(std::move(%s0%s), %s);' % (
        ret, ('.value()' if optional_ret0 else ''),
        postconv.Initializer(
            func_ast.returns[0].type,
            typepostconversion,
            marked_non_raising=func_ast.marked_non_raising))
  elif return_self or ctxmgr == '__enter__@':
    yield I+'Py_INCREF(self);'
    yield I+'return self;'
  else:
    yield I+'Py_RETURN_NONE;'
  yield '}'


def _GenExceptionTry():
  yield I+'PyObject* err_type = nullptr;'
  yield I+'std::string err_msg{"C++ exception"};'
  yield I+'try {'


def _GenExceptionCatch():
  yield I+'} catch(const std::exception& e) {'
  yield I+I+'err_type = PyExc_RuntimeError;'
  yield I+I+'err_msg += std::string(": ") + e.what();'
  yield I+'} catch (...) {'
  yield I+I+'err_type = PyExc_RuntimeError;'
  yield I+'}'


def _GenExceptionRaise():
  yield I+'if (err_type) {'
  yield I+I+'PyErr_SetString(err_type, err_msg.c_str());'
  yield I+I+'return nullptr;'
  yield I+'}'


def _VirtualFunctionCall(fname, f, pyname, abstract, postconvinit):
  """Generate virtual redirector call wrapper from AST.FuncDecl f."""
  name = f.name.cpp_name
  ret = astutils.FuncReturnType(f, true_cpp_type=True)
  arg = astutils.FuncParamStr(f, 'a', true_cpp_type=True)
  mod = ['']
  if f.cpp_const_method: mod.append('const')
  if f.cpp_noexcept: mod.append('noexcept')
  yield ''
  yield I+'%s %s%s%s override {' % (ret, fname, arg, ' '.join(mod))
  params = astutils.TupleStr('std::move(a%i)' % i for i in range(
      len(f.params) + len(f.returns) - (ret != 'void')))
  yield I+I+'SafeAttr impl(self(), "%s");' % f.name.native
  yield I+I+'if (impl.get()) {'
  ret_st = 'return ' if ret != 'void' else ''
  yield I+I+I+'%s::clif::callback::Func<%s>(impl.get(), {%s})%s;' % (
      ret_st, ', '.join(
          [ret] +
          list(astutils.ExactTypeOrType(a) for a in f.params) +
          list(astutils.FuncReturns(f))),
      ', '.join(postconvinit(a.type) for a in f.params), params)
  yield I+I+'} else {'
  if abstract:
    # This is only called from C++. Since f has no info if it is pure virtual,
    # we can't always generate the call, so we always fail in an abstract class.
    yield I+I+I+('Py_FatalError("@virtual method %s.%s has no Python '
                 'implementation.");' % (pyname, f.name.native))
    # In Python 2 Py_FatalError is not marked __attribute__((__noreturn__)),
    # so to avoid -Wreturn-type warning add extra abort(). It does not hurt ;)
    yield I+I+I+'abort();'
  else:
    yield I+I+I+ret_st + name + params + ';'
  yield I+I+'}'
  yield I+'}'


def CastAsCapsule(wrapped_cpp, pointer_name, wrapper):
  yield ''
  yield '// Implicit cast this as %s*' % pointer_name
  yield 'static PyObject* %s(PyObject* self) {' % wrapper
  yield I+'%s* p = ::clif::python::Get(%s);' % (pointer_name, wrapped_cpp)
  yield I+'if (p == nullptr) return nullptr;'
  yield I+('return PyCapsule_New(p, "%s", nullptr);') % pointer_name
  yield '}'


class _NewIter(object):
  """Generate the new_iter function."""
  name = 'new_iter'

  def __call__(self, wrapped_iter, ns, wrapper, wrapper_type):
    yield ''
    yield 'PyObject* new_iter(PyObject* self) {'
    yield I+'if (!ThisPtr(self)) return nullptr;'
    yield I+'%s* it = PyObject_New(%s, %s);' % (wrapper, wrapper, wrapper_type)
    yield I+'if (!it) return nullptr;'
    yield I+'using std::equal_to;  // Often a default template argument.'
    yield I+'new(&it->iter) %siterator(MakeStdShared(%s));' % (ns, wrapped_iter)
    yield I+'return %s(it);' % _Cast()
    yield '}'

NewIter = _NewIter()  # pylint: disable=invalid-name


class _IterNext(object):
  """Generate the iternext function."""
  name = 'iternext'

  def __call__(self, wrapped_iter, is_async, postconversion):
    """Generate tp_iternext method implementation."""
    yield ''
    yield 'PyObject* iternext(PyObject* self) {'
    if is_async:
      yield I+'PyThreadState* _save;'
      yield I+'Py_UNBLOCK_THREADS'
    yield I+'auto* v = %s.Next();' % wrapped_iter
    if is_async:
      yield I+'Py_BLOCK_THREADS'
    yield I+'return v? Clif_PyObjFrom(*v, %s): nullptr;' % postconversion
    yield '}'

IterNext = _IterNext()  # pylint: disable=invalid-name


def FromFunctionDef(ctype, wdef, wname, flags, doc):
  """PyCFunc definition."""
  assert ctype.startswith('std::function<'), repr(ctype)
  return 'static PyMethodDef %s = %s;' % (wdef, _DefLine('', wname, flags, doc))


def VarGetter(name, cfunc, error, cvar, pc, is_extend=False):
  """Generate var getter."""
  xdata = '' if cfunc else ', void* xdata'
  yield ''
  yield 'static PyObject* %s(PyObject* self%s) {' % (name, xdata)
  if error and not is_extend:
    yield I+error+'return nullptr;'
  yield I+'return Clif_PyObjFrom(%s, %s);' % (cvar, pc)
  yield '}'


def VarSetter(name, cfunc, error, cvar, v, csetter, as_str, is_extend=False):
  """Generate var setter.

  Args:
    name: setter function name
    cfunc: (True/False) generate setter as a CFunction
    error: C++ condition to return error if any
    cvar: C var name to set new value to directly
    v: VAR AST
    csetter: C++ call expression to set var (without '(newvalue)') if any
    as_str: Python str -> C str function (different for Py2/3)
    is_extend: True for @extend properties in the .clif file.

  Yields:
     Source code for setter function.
  """
  yield ''
  if cfunc:
    yield 'static PyObject* %s(PyObject* self, PyObject* value) {' % name
    ret_error = 'return nullptr;'
    ret_ok = 'Py_RETURN_NONE;'
  else:
    yield ('static int %s(PyObject* self, PyObject* value, void* xdata) {'
           % name)
    ret_error = 'return -1;'
    ret_ok = 'return 0;'
    yield I+'if (value == nullptr) {'
    yield I+I+('PyErr_SetString(PyExc_TypeError, "Cannot delete the'
               ' %s attribute");' % v.name.native)
    yield I+I+ret_error
    yield I+'}'
    if csetter:
      # Workaround BUG "v.type.cpp_type not updated by Matcher", so get p[0].
      yield I+'%s cval;' % v.cpp_set.params[0].type.cpp_type
      yield I+'if (Clif_PyObjAs(value, &cval)) {'
      if error:
        yield I+I+error+ret_error
      if is_extend:
        yield I+I+csetter + '(*cpp, cval);'
      else:
        yield I+I+csetter + '(cval);'
      yield I+I+ret_ok
      yield I+'}'
  if not csetter:
    if error:
      yield I+error+ret_error
    yield I+'if (Clif_PyObjAs(value, &%s)) ' % cvar + ret_ok
  yield I+'PyObject* s = PyObject_Repr(value);'
  yield I+('PyErr_Format(PyExc_ValueError, "%s is not valid for {}:{}", s? {}'
           '(s): "input");').format(v.name.native, v.type.lang_type, as_str)
  yield I+'Py_XDECREF(s);'
  yield I+ret_error
  yield '}'


def _Cast(t='PyObject'):
  assert not t.endswith('*')
  return 'reinterpret_cast<%s*>' % t
