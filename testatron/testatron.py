'''
This program allows the user to run any or all of the 
EMTG regression tests in the test suite. 

--------------------------------------- USAGE -------------------------------------------
1) Open anaconda prompt & change directory to the testatron folder 
   e.g. > cd C:\emtg\testatron
   
2) Use 'python testatron.py -h' to see help on command-line args
'''

'''
Import utilities
'''
from os import makedirs, getcwd, listdir, walk, path
from time import strftime
import ast
import pdb # Python debugger; use q in command line to quit
import sys
import argparse
import subprocess
from options_preparation import apply_test_options_overrides
from test_selection import (
    SMOKE_ATTRIBUTES_TO_IGNORE,
    SMOKE_DEFAULT_TOLERANCE,
    SMOKE_TEST_CASES,
    UNIT_TEST_FOLDERS,
    make_tests_list,
)

# Can't use a relative path because we use importlib later
test_directory = getcwd().replace('\\','/') + '/tests/' 


'''
PARSE USER INPUT ------------------------------------------------------------------------
'''
test_cases = [];

# use argparse package
parser = argparse.ArgumentParser(description="EMTG test system driver. Select only ONE of -c, -f, --failure, -u, -m, -a, --update_truths. If none is provided, -a is the default")

# arguments

# path to emtg executable
parser.add_argument('-e', '--emtg', dest = 'emtgPath', 
    default = 'c:/emtg/bin/EMTGv9.exe',
    help = 'Set the path to the EMTG executable (default: c:/emtg/bin/EMTGv9.exe)')
    
# path to pyemtg
parser.add_argument('-p', '--pyemtg', dest = 'pyemtgPath',
    default = 'c:/emtg/PyEMTG/',
    help = 'Set the path to PyEMTG (default: c:/emtg/PyEMTG/)')
    
# run specific cases
parser.add_argument('-c', '--cases', dest = 'runCases', nargs = '*',
    help = 'Specify full path to one or more individual cases or folders to run. Separate multiple cases with spaces only. DO NOT include file extension for files. DO NOT put in brackets. DO NOT use commas.')

# run specific folders of cases
parser.add_argument('-f', '--folders', dest = 'runFolders', nargs = '*',
    help = 'Run all cases in one or more folders. Folders must be in testatron/tests. End folder names with a /. Separate multiple folders with spaces only. DO NOT prepend folder name with /path/to/testatron/tests. DO NOT put in quotes. DO NOT put in brackets. DO NOT use commas.')

# run failure cases from csv
parser.add_argument('--failure', dest = 'runFailure', nargs = 1,
    help = 'Run all tests that failed, as per a failed_tests.csv file. Give path to folder that contains failed_tests.csv, ending with a /. DO NOT include the "failed_tests.csv" file in the path to the file.')

# run unit tests (ie, all except failures)
parser.add_argument('-u', '--unit', dest = 'runUnit', nargs = '?',
    const = 1, default = 0, type = int,
    help = 'If active, run unit tests (currently, this means all non-failing tests)')

# run smoke tests
parser.add_argument('--smoke', dest = 'runSmoke', nargs = '?',
    const = 1, default = 0, type = int,
    help = 'Run the vetted fast EMTG executable smoke cases with smoke comparison defaults.')
    
# run mission tests only
parser.add_argument('-m', '--mission', dest = 'runMission', nargs = '?',
    const = 1, default = 0, type = int,
    help = 'If active, run mission tests')
    
# run all cases
parser.add_argument('-a', '--all', dest = 'runAll', nargs = '?',
    const = 1, default = 0, type = int,
    help = 'If active, run all cases. This is also the default behavior if no other specific behavior is requested.')
    
# update truth .emtg files
parser.add_argument('--update_truths', dest = 'updateTruths', nargs = '?',
    const = 1, default = 0, type = int,
    help = 'If selected, the test system IS NOT RUN. Instead, all test .emtgopt files are executed, and all truth .emtg files are replaced with the results of executing the .emtgopt files. This option is useful if something has changed that breaks every test. For example, if a new attribute has been added to the PyEMTG Mission class.')

# attributes to ignore when running comparatron
parser.add_argument('--ignore', dest = 'attributes_to_ignore', nargs = '*',
	default = [],
    help = 'List attributes to ignore when running Comparatron. Start Mission attributes with M., Journey attributes with J., and MissionEvent attributes with E.. DO NOT put in quotes. DO NOT put in brackets. DO NOT use commas.')

parser.add_argument('--default_tolerance', dest = 'default_tolerance',
    default = 1.0e-10, type = float,
    help = 'Default numeric tolerance passed to Comparatron (default: 1.0e-10).')

parser.add_argument('--emtg_feasibility_tolerance', dest = 'emtg_feasibility_tolerance',
    default = None, type = float,
    help = 'Override EMTG solver feasibility tolerance in generated options files.')

parser.add_argument('--emtg_optimality_tolerance', dest = 'emtg_optimality_tolerance',
    default = None, type = float,
    help = 'Override EMTG solver optimality tolerance in generated options files.')

parser.add_argument('--emtg_major_iterations', dest = 'emtg_major_iterations',
    default = None, type = int,
    help = 'Override EMTG solver major iteration limit in generated options files.')

parser.add_argument('--emtg_max_run_time', dest = 'emtg_max_run_time',
    default = None, type = int,
    help = 'Override EMTG solver max run time in seconds in generated options files.')

parser.add_argument('--emtg_quiet_nlp', dest = 'emtg_quiet_nlp',
    default = None, type = int, choices = [0, 1],
    help = 'Override EMTG quiet_NLP in generated options files. Use 1 to suppress solver iteration logs.')


args = parser.parse_args()

EMTG_path = args.emtgPath
PyEMTG_path = args.pyemtgPath

# we want run all to be the default behavior
runCases = 0
runFolders = 0
runFailure = 0
runUnit = 0
runMission = 0
runSmoke = 0
updateTruths = 0
runAll = args.runAll
testCases = None
testFolders = None
failure = None
if (args.runCases == None and args.runFolders == None and args.runFailure == None and args.runUnit == 0 and args.runMission == 0 and args.runSmoke == 0 and args.updateTruths == 0):
    runAll = 1
    run_type = 'all'
    
if (args.runCases != None):
    runCases = 1
    testCases = args.runCases
    run_type = 'cases'
elif (args.runFolders != None):
    runFolders = 1
    testFolders = args.runFolders
    run_type = 'folders'
elif (args.runFailure != None):
    runFailure = 1
    failure = args.runFailure
    run_type = 'failed'
elif (args.runUnit == 1):
    runUnit = 1
    run_type = 'folders'
elif (args.runSmoke == 1):
    runSmoke = 1
    run_type = 'cases'
elif (args.runMission == 1):
    runMission = 1
    run_type = 'folders'
elif (args.updateTruths == 1):
    updateTruths = 1
    run_type = 'all'

if runCases == 1:
    print('Running user cases')
    for casse in testCases:
        test_cases.append(casse) 
        
elif runFolders == 1:
    print('Running all cases in user-specified folders')
    for folderr in testFolders:
        test_cases.append(folderr) 
        
elif runFailure == 1:
    print('Running failed tests')
    test_cases.append(failure[0]) # Needs to be a *.csv file from the testatron\output dir
    
elif runUnit == 1:
    print('Running unit (feature) tests')
    run_type = 'folders'
    test_cases = UNIT_TEST_FOLDERS

elif runSmoke == 1:
    print('Running smoke tests')
    run_type = 'cases'
    test_cases = SMOKE_TEST_CASES
    if args.default_tolerance == parser.get_default('default_tolerance'):
        args.default_tolerance = SMOKE_DEFAULT_TOLERANCE
    if args.attributes_to_ignore == parser.get_default('attributes_to_ignore'):
        args.attributes_to_ignore = SMOKE_ATTRIBUTES_TO_IGNORE
    
elif runMission == 1:
    print('Running mission tests')
    run_type = 'folders'
    test_cases = ['mission_tests']
    
elif updateTruths == 1:
    print('Updating truth .emtg files')
    run_type = 'all'
    test_cases = []
    
else: # Else run all b/c can't use 'cases' without actual cases
    print('Running all tests')
    run_type   = 'all'
    test_cases = []




'''
METHOD: MAKE THE LIST OF TESTS ----------------------------------------------------------
'''
def MakeTestsList(test_cases):
    return make_tests_list(test_cases, run_type, test_directory, getcwd().replace('\\','/'))
'''
------------------------------------------------------------------------------------------
'''

# Create tests list
tests_to_run = MakeTestsList(test_cases)

print(tests_to_run)

# create directories for output if we are actually running the test system
if updateTruths == 0:
    # Get the current epoch
    now = strftime('%c')
    now_formatted = now.replace(' ','_').replace(':','')

    # Create an output directory and summary file
    outputdir = path.join(getcwd(), 'output', now_formatted)
    print('OUTPUT_DIRECTORY: '+outputdir)
    makedirs(outputdir, exist_ok=True)

    # Overall summary file with all run tests & their ?success status
    summaryFile = open(outputdir + '/test_results.csv','w') 
    summaryFile.write('Beginning test run ' + now + '\n\n')

    # File that only records when a test fails, records the test 
    # and all mission objects that failed
    failFile = open(outputdir + '/failed_tests.csv','w') 
    failFile.write('Beginning test run ' + now + '\n\n')

'''
Run the tests ---------------------------------------------------------------------------

'''
import os
import sys
sys.path.append(test_directory)
sys.path.append(PyEMTG_path)
import importlib

import Mission
import MissionOptions

failedTests      = 0  # Continuous counter of the number of failed tests
failedTests_list = [] # List of file paths for failed tests

for test in tests_to_run:
    test_name=test.split('/')[-1]
    if updateTruths == 1:
        print('Updating test "' + test + '"...')
    else:
        summaryFile.write('Beginning test "' + test + '"...')

    if path.isfile(test+'.emtgopt'): #True: #try:
        testOptions = MissionOptions.MissionOptions(test + '.emtgopt') # Emtg ops object

        apply_test_options_overrides(
            testOptions,
            test_directory,
            outputdir if updateTruths == 0 else None,
            test,
            update_truths=(updateTruths == 1),
            emtg_feasibility_tolerance=args.emtg_feasibility_tolerance,
            emtg_optimality_tolerance=args.emtg_optimality_tolerance,
            emtg_major_iterations=args.emtg_major_iterations,
            emtg_max_run_time=args.emtg_max_run_time,
            emtg_quiet_nlp=args.emtg_quiet_nlp,
        )

        # Save updated emtg options file and always write out all of the options to the file
        if updateTruths == 1:
            # if updating truths, overwrite the .emtgopt files
            testOptions.write_options_file(test + '.emtgopt', not testOptions.print_only_non_default_options)
        else:
            # if running tests, save to output directory
            testOptions.write_options_file(outputdir + '/' + test_name + '.emtgopt', not testOptions.print_only_non_default_options)

        if updateTruths == 1:
            emtg_options_file = test + '.emtgopt'
        else:
            emtg_options_file = outputdir + '/' + test_name + '.emtgopt'

        try:
            # Run EMTG. Use subprocess so failures are visible to the harness.
            emtg_run = subprocess.run([EMTG_path, emtg_options_file], check = False)
        except Exception as error:
            if updateTruths == 1:
                print('\nFAILURE to run "' + test + '" options file: ' + str(error) + '\n\n')
            else:
                summaryFile.write('\nFAILURE to run "' + test + '" options file: ' + str(error) + '\n\n')
                failFile.write('\nFAILURE to run "' + test + '" options file: ' + str(error) + '\n\n')
                failedTests += 1
                failedTests_list.append(test)
            continue

        if emtg_run.returncode != 0:
            if updateTruths == 1:
                print('\nFAILURE running "' + test + '": EMTG exited with code ' + str(emtg_run.returncode) + '.\n\n')
            else:
                summaryFile.write('\nFAILURE running "' + test + '": EMTG exited with code ' + str(emtg_run.returncode) + '.\n\n')
                failFile.write('\nFAILURE running "' + test + '": EMTG exited with code ' + str(emtg_run.returncode) + '.\n\n')
                failedTests += 1
                failedTests_list.append(test)
            continue
        
        if updateTruths == 0:
            nominal_output_file = outputdir + '/' + test_name + '.emtg'
            failure_output_file = outputdir + '/FAILURE_' + test_name + '.emtg'
            if not path.isfile(nominal_output_file):
                if path.isfile(failure_output_file):
                    summaryFile.write('\nFAILURE: EMTG wrote infeasible failure output for "' + test + '" at "' + failure_output_file + '".\n\n')
                    failFile.write('\nFAILURE: EMTG wrote infeasible failure output for "' + test + '" at "' + failure_output_file + '".\n\n')
                else:
                    summaryFile.write('\nFAILURE: EMTG did not write expected output "' + nominal_output_file + '" for "' + test + '".\n\n')
                    failFile.write('\nFAILURE: EMTG did not write expected output "' + nominal_output_file + '" for "' + test + '".\n\n')
                failedTests += 1
                failedTests_list.append(test)
                continue

            try:
                # Post-process
                testMission = Mission.Mission(nominal_output_file)
            except:
                summaryFile.write('\nFAILURE to parse output for "' + test + '" options file.\n\n')
                failFile.write('\nFAILURE to parse output for "' + test + '" options file.\n\n')
                failedTests += 1
                failedTests_list.append(test)
                continue
                    
            print("\nRunning comparator...\n")
            # Run standard comparator that checks every mission event
            try:
                success, output = testMission.Comparatron(baseline_path = test + '.emtg',\
                          csv_file_name = outputdir +'/' + test_name + '_comparison.csv',\
                                                                      full_output=False,\
                                                                       tolerance_dict={},\
                                                              default_tolerance = args.default_tolerance,\
                                                              attributes_to_ignore = args.attributes_to_ignore)                                                        

                if success:
                    summaryFile.write('successful\n\n')
                else:
                    summaryFile.write('failed\n\n')
                    failFile.write('\nTest "' + test + '" failed for parameters:\n')
                    failFile.close() 

                    # Writes output to the end of the failFile
                    output.loc[output['Match']==False].to_csv(outputdir + '/failed_tests.csv',\
                                                                         mode='a', index=False)

                    # Reopens the failFile so that the driver can write the next test
                    failFile = open(outputdir + '/failed_tests.csv','a') 
                    failFile.write('\n')
                    failedTests += 1
                    failedTests_list.append(test)
            except:
                summaryFile.write('\nFAILURE to compare "' + test + '" options file.\n\n')
                failFile.write('\nFAILURE to compare "' + test + '" options file.\n\n')
                failedTests += 1
                failedTests_list.append(test)
    else:
        if updateTruths == 0:
            summaryFile.write('\nFAILURE to load "' + test + '" options file.\n\n')
            failFile.write('\nFAILURE to load "' + test + '" options file.\n\n')
            failedTests += 1
            failedTests_list.append(test)

if updateTruths == 1:
    # clean up: don't keep bspwriter.py, XFfile.csv, mission_maneuver_spec, mission_target_spec, cmd, or emtg_spacecraftopt files
    test_folders = []
    test_folders = next(walk(test_directory))[1] 
    test_folders.append('')
    for folder in test_folders:
        filesToDelete = [] # list of files to delete
        filesInDirectory = listdir(test_directory + folder) # all files in this directory
        
        # gather up all the files to delete
        
        files = [file for file in filesInDirectory if file.endswith(".py")] # get rid of bspwriter.py
        filesToDelete.extend(files)
        
        files = [file for file in filesInDirectory if file.endswith("XFfile.csv")] # get rid of XFfile.csv
        filesToDelete.extend(files)
        
        files = [file for file in filesInDirectory if file.endswith(".mission_maneuver_spec")] # get rid of .mission_maneuver_spec
        filesToDelete.extend(files)
        
        files = [file for file in filesInDirectory if file.endswith(".mission_target_spec")] # get rid of .mission_target_spec
        filesToDelete.extend(files)
        
        files = [file for file in filesInDirectory if file.endswith(".cmd")] # get rid of .cmd
        filesToDelete.extend(files)
        
        files = [file for file in filesInDirectory if file.endswith(".emtg_spacecraftopt")] # get rid of .emtg_spacecraftopt
        filesToDelete.extend(files)
        
        # loop through the files we found
        for file in filesToDelete:
            pathToFile = os.path.join(test_directory + folder, file) # add the path to the file
            #print("Want to delete " + pathToFile)
            os.remove(pathToFile) # delete the file

        
    
    print("Finished updating truth files")
else:
    # Find and write the time that the tests finished running
    end = strftime('%c')
    summaryFile.write('Finished test run ' + end)
    failFile.write('Finished test run ' + end + '\n')

    # Add a list of failed cases to the end of the failFile. 
    # This can be used for future testatron runs
    failFile.write('\nList of failed runs:\n')
    for failTest in failedTests_list:
        failFile.write(failTest + '\n')

    print("\nAll tests completed.\n")
    print("Failed " + str(failedTests) + " test(s).")
    for failTest in failedTests_list:
        print(failTest)

    summaryFile.close()
    failFile.close()




