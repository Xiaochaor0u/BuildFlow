package com.devops.demo.controller;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import java.util.List;

import static org.mockito.Mockito.*;

import com.devops.demo.entity.Customer;
import com.devops.demo.service.CustomerService;
import com.fasterxml.jackson.databind.ObjectMapper;

@WebMvcTest(Controller.class)
class CustomerControllerTest {

	@Autowired
	private MockMvc mockMvc;

	@MockBean
	private CustomerService service;

	@Autowired
	private ObjectMapper mapper;

	@Test
	void testCreateCustomer() throws Exception {
		Customer c = new Customer(1, "Jude", "Street", 38);
		when(service.saveCustomer(c)).thenReturn(c);

		mockMvc.perform(
				post("/api/v1/customers/create").contentType("application/json").content(mapper.writeValueAsString(c)))
				.andExpect(status().isOk()).andExpect(jsonPath("$.name").value("Jude"));
	}

	@Test
	void testGetAllCustomers() throws Exception {
		when(service.getAllCustomers()).thenReturn(List.of(new Customer(1, "Jude", "Street", 38)));

		mockMvc.perform(get("/api/v1/customers/all")).andExpect(status().isOk())
				.andExpect(jsonPath("$[0].id").value(1));
	}

	@Test
	void testGetCustomerById() throws Exception {
		Customer c = new Customer(1, "Jude", "Street", 38);
		when(service.findById(1)).thenReturn(c);

		mockMvc.perform(get("/api/v1/customers/1")).andExpect(status().isOk())
				.andExpect(jsonPath("$.name").value("Jude"));
	}
}