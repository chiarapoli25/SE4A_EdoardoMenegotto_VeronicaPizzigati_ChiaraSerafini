if(NOT DEFINED EXPERIMENT_EXECUTABLE OR NOT DEFINED OUTPUT_DIRECTORY OR
   NOT DEFINED BASE_NAME OR NOT DEFINED EXPECTED_LINES)
    message(FATAL_ERROR "standard experiment smoke test is missing variables")
endif()

file(REMOVE_RECURSE "${OUTPUT_DIRECTORY}")
file(MAKE_DIRECTORY "${OUTPUT_DIRECTORY}")
execute_process(
    COMMAND "${EXPERIMENT_EXECUTABLE}"
    WORKING_DIRECTORY "${OUTPUT_DIRECTORY}"
    RESULT_VARIABLE result
    OUTPUT_VARIABLE output
    ERROR_VARIABLE error
)
if(NOT result EQUAL 0)
    message(FATAL_ERROR "experiment failed:\n${output}\n${error}")
endif()

set(csv_path "${OUTPUT_DIRECTORY}/experiment_results/${BASE_NAME}.csv")
if(NOT EXISTS "${csv_path}")
    message(FATAL_ERROR "expected CSV was not created: ${csv_path}\n${output}\n${error}")
endif()
file(STRINGS "${csv_path}" csv_lines)
list(LENGTH csv_lines line_count)
if(NOT line_count EQUAL EXPECTED_LINES)
    message(FATAL_ERROR "CSV has ${line_count} lines, expected ${EXPECTED_LINES}")
endif()

if(EXPECT_PNG)
    set(png_path "${OUTPUT_DIRECTORY}/experiment_results/${BASE_NAME}.png")
    if(NOT EXISTS "${png_path}")
        message(FATAL_ERROR "expected PNG was not created: ${png_path}\n${output}\n${error}")
    endif()
endif()
