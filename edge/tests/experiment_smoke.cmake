if(NOT DEFINED EXPERIMENT_EXECUTABLE OR NOT DEFINED OUTPUT_DIRECTORY OR
   NOT DEFINED BASE_NAME OR NOT DEFINED EXPECTED_LINES OR NOT DEFINED SOIL)
    message(FATAL_ERROR "experiment smoke test is missing required variables")
endif()

file(REMOVE_RECURSE "${OUTPUT_DIRECTORY}")
file(MAKE_DIRECTORY "${OUTPUT_DIRECTORY}")

if(DURATION STREQUAL "day")
    set(duration_choice 1)
elseif(DURATION STREQUAL "week")
    set(duration_choice 2)
else()
    message(FATAL_ERROR "unsupported smoke-test duration: ${DURATION}")
endif()

if(SOIL STREQUAL "aerated-universal")
    set(soil_choice 1)
elseif(SOIL STREQUAL "draining")
    set(soil_choice 2)
elseif(SOIL STREQUAL "organic-retentive")
    set(soil_choice 3)
else()
    message(FATAL_ERROR "unsupported smoke-test soil: ${SOIL}")
endif()

set(input_text "${duration_choice}\n${soil_choice}\n")
string(APPEND input_text "31415\n")
set(input_path "${OUTPUT_DIRECTORY}/input.txt")
file(WRITE "${input_path}" "${input_text}")

if(NO_GNUPLOT)
    execute_process(
        COMMAND "${CMAKE_COMMAND}" -E env "PATH=/smarthydro-no-gnuplot"
                "${EXPERIMENT_EXECUTABLE}"
        INPUT_FILE "${input_path}"
        WORKING_DIRECTORY "${OUTPUT_DIRECTORY}"
        RESULT_VARIABLE result
        OUTPUT_VARIABLE output
        ERROR_VARIABLE error
    )
else()
    execute_process(
        COMMAND "${EXPERIMENT_EXECUTABLE}"
        INPUT_FILE "${input_path}"
        WORKING_DIRECTORY "${OUTPUT_DIRECTORY}"
        RESULT_VARIABLE result
        OUTPUT_VARIABLE output
        ERROR_VARIABLE error
    )
endif()

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

if(CHECK_DARKNESS)
    list(GET csv_lines 1 first_sample)
    list(GET csv_lines -1 last_sample)
    if(NOT first_sample MATCHES ",0\\.000000$" OR
       NOT last_sample MATCHES ",0\\.000000$")
        message(FATAL_ERROR "night PPFD is not zero: ${first_sample} / ${last_sample}")
    endif()
endif()

set(png_path "${OUTPUT_DIRECTORY}/experiment_results/${BASE_NAME}.png")
if(EXPECT_PNG AND NOT EXISTS "${png_path}")
    message(FATAL_ERROR "expected PNG was not created: ${png_path}\n${output}\n${error}")
endif()
if(NO_GNUPLOT AND EXISTS "${png_path}")
    message(FATAL_ERROR "PNG was unexpectedly created without gnuplot")
endif()
