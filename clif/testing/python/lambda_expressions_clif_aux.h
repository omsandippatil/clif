/*
 * Copyright 2021 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
#ifndef THIRD_PARTY_CLIF_TESTING_PYTHON_LAMBDA_EXPRESSIONS_CLIF_AUX_H_
#define THIRD_PARTY_CLIF_TESTING_PYTHON_LAMBDA_EXPRESSIONS_CLIF_AUX_H_

#include <Python.h>

#include <memory>
#include <vector>

#include "clif/testing/lambda_expressions.h"


namespace clif_testing {

inline std::unique_ptr<TestExtendCtor> TestExtendCtor__extend__init__(
    int i, Arg arg = {100}) {
  auto res = std::make_unique<TestExtendCtor>();
  res->value = i + arg.value;
  return res;
}

inline std::unique_ptr<ExtendedCtorTakesVector>
ExtendedCtorTakesVector__extend__init__(const std::vector<int>& vec) {
  auto res = std::make_unique<ExtendedCtorTakesVector>();
  res->value = vec;
  return res;
}

inline std::unique_ptr<ExtendedCtorTakesPyObj>
ExtendedCtorTakesPyObj__extend__init__(PyObject *obj) {
  auto res = std::make_unique<ExtendedCtorTakesPyObj>();
  res->value = PyLong_AsLong(obj);
  if (res->value == -1 && PyErr_Occurred()) {
    PyErr_Clear();
  }
  return res;
}

}  // namespace clif_testing

#endif  // THIRD_PARTY_CLIF_TESTING_PYTHON_LAMBDA_EXPRESSIONS_CLIF_AUX_H_
