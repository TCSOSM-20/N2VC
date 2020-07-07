<!--- Copyright 2020 Canonical Ltd.

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

     http://www.apache.org/licenses/LICENSE-2.0

     Unless required by applicable law or agreed to in writing, software
     distributed under the License is distributed on an "AS IS" BASIS,
     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
     See the License for the specific language governing permissions and
     limitations under the License. --->


# N2VC Unit Testing Guideline

## Use `test_libjuju.py` as a guideline

Even though the Test Cases still have plenty of potential improvements we feel like this file is the most polished of all of them. Therefore it should be used as a baseline of any future tests or changes in current tests for what is the minimum standard.

## Try to use mock as much as possible

There are some cases where FakeClasses (which still inherit from Mock classes) are used. This is only for the cases where the construction of the object requires to much additional mocking. Using standard mocks gives more testing possibilities.

## Separate your Test Cases into different classes

It is preferrable to have a TestCase Class for each method and several test methods to test different scenarios. If all of the classes need the same setup a Parent TestCase class can be created with a setUp method and afterwards the other TestCases can inherit from it like this:

```python
class GetControllerTest(LibjujuTestCase):

    def setUp(self):
        super(GetControllerTest, self).setUp()
```

## Things to assert

It is more important to actually assert the important logic than have a high code coverage but not actually testing the code.

These are some of the things that should be always asserted:

* Assert all Exceptions are launched correctly.
* Assert the return values are the expected ones for **both** succesfull executions and unsuccesful ones.
* Assert that all important calls have been called the correct amount of time and with the correct arguments.
* Assert that when the method is failing the correct log messages are posted.
* Assert that all things to need to be disconnected after execution are correctly disconnected.

