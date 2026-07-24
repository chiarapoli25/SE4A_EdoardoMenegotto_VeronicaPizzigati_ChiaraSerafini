if(NOT DEFINED EXPERIMENT_EXECUTABLE OR NOT DEFINED OUTPUT_DIRECTORY)
    message(FATAL_ERROR "actuator simulation smoke test is missing variables")
endif()

file(REMOVE_RECURSE "${OUTPUT_DIRECTORY}")
file(MAKE_DIRECTORY "${OUTPUT_DIRECTORY}")
set(input_path "${OUTPUT_DIRECTORY}/input.txt")
file(WRITE "${input_path}" "1\n0.5\n1\n0\n1\n2\n2\n68\nq\n")

# Il terminale testuale evita di aprire una finestra grafica durante CTest.
execute_process(
    COMMAND ${CMAKE_COMMAND} -E env GNUTERM=dumb "${EXPERIMENT_EXECUTABLE}"
    WORKING_DIRECTORY "${OUTPUT_DIRECTORY}"
    INPUT_FILE "${input_path}"
    RESULT_VARIABLE result
    OUTPUT_VARIABLE output
    ERROR_VARIABLE error
)
if(NOT result EQUAL 0)
    message(FATAL_ERROR "actuator simulation experiment failed:\n${output}\n${error}")
endif()

foreach(expected_text
    "Pompa: dose richiesta 0.5 L, OFF, portata 0 L/h, ultimo volume 0.5 L"
    "Valvola nitrogen: CLOSED, portata 0 mL/h, ultimo volume 5 mL"
    "Valvola potassium: CLOSED, portata 0 mL/h, ultimo volume 5 mL"
    "Valvola ph-down: CLOSED, portata 0 mL/h, ultimo volume 5 mL"
    "Illuminazione: 68% -> 136 W"
    "Simulazione terminata.")
    string(FIND "${output}" "${expected_text}" position)
    if(position EQUAL -1)
        message(FATAL_ERROR
            "missing expected text '${expected_text}':\n${output}\n${error}")
    endif()
endforeach()

if(EXISTS "${OUTPUT_DIRECTORY}/experiment_results")
    message(FATAL_ERROR "the live experiment must not create result files")
endif()
