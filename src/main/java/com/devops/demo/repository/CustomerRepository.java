package com.devops.demo.repository;

import org.springframework.stereotype.Repository;

import com.devops.demo.entity.Customer;

import java.util.ArrayList;
import java.util.List;

@Repository
public class CustomerRepository {

    private final List<Customer> list = new ArrayList<Customer>();

    public List<Customer> getAllCustomers() {
        return list;
    }

    public Customer findById(int id) {
        for(Customer c: list) {
            if(c.getId() == id) {
                return c;
            }
        }
        return null;
    }

    public Customer save(Customer customer) {
        Customer c = new Customer();
        c.setId(customer.getId());
        c.setName(customer.getName());
        c.setAge(customer.getAge());
        c.setAddress(customer.getAddress());
        list.add(c);
        return c;
    }
}
