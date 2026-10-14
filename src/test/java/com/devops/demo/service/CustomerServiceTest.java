package com.devops.demo.service;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import com.devops.demo.entity.Customer;
import com.devops.demo.repository.CustomerRepository;

import static org.mockito.Mockito.*;

import java.util.List;

public class CustomerServiceTest {
	@Test
	void testSaveCustomer() {
		CustomerRepository repo = mock(CustomerRepository.class);
		CustomerService service = new CustomerService();
		service.customerRepository = repo;

		Customer c = new Customer(1, "Jude", "Street", 38);
		when(repo.save(c)).thenReturn(c);

		Customer saved = service.saveCustomer(c);

		assertEquals("Jude", saved.getName());
		verify(repo, times(1)).save(c);
	}

	@Test
	void testGetAllCustomers() {
		CustomerRepository repo = mock(CustomerRepository.class);
		CustomerService service = new CustomerService();
		service.customerRepository = repo;

		when(repo.getAllCustomers()).thenReturn(List.of(new Customer(1, "Jude", "Street", 38)));

		List<Customer> list = service.getAllCustomers();

		assertEquals(1, list.size());
		verify(repo).getAllCustomers();
	}

}
