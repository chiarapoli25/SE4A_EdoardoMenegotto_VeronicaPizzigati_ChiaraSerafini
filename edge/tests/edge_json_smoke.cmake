if(NOT DEFINED EDGE_EXECUTABLE OR NOT DEFINED RECIPE_PATH)
    message(FATAL_ERROR "edge JSON smoke test is missing variables")
endif()

execute_process(
    COMMAND
        "${EDGE_EXECUTABLE}"
        --recipe "${RECIPE_PATH}"
        --steps 2
        --step-seconds 900
        --output json
    RESULT_VARIABLE result
    OUTPUT_VARIABLE output
    ERROR_VARIABLE error
)

if(NOT result EQUAL 0)
    message(FATAL_ERROR "edge JSON execution failed:\n${output}\n${error}")
endif()

foreach(expected_text
    "\"recipe\":{\"file\":"
    "\"steps\":["
    "\"sensors\":{"
    "\"decisions\":{"
    "\"actuators\":{"
    "\"environment\":{")
    string(FIND "${output}" "${expected_text}" position)
    if(position EQUAL -1)
        message(FATAL_ERROR "missing JSON field ${expected_text}:\n${output}")
    endif()
endforeach()

string(REGEX MATCHALL "\"cycle\":[0-9]+" cycles "${output}")
list(LENGTH cycles cycle_count)
if(NOT cycle_count EQUAL 2)
    message(FATAL_ERROR "edge JSON returned ${cycle_count} cycles, expected 2")
endif()

string(FIND "${output}" "SmartHydro Edge Controller\nVersion:" human_banner)
if(NOT human_banner EQUAL -1)
    message(FATAL_ERROR "human-readable banner leaked into JSON output")
endif()
