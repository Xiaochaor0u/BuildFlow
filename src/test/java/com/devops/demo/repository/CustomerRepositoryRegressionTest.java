package com.devops.demo.repository;

import com.devops.demo.entity.Customer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;

/**
 * 真实回归测试：直接使用真实的 CustomerRepository（不使用 Mockito），
 * 覆盖审查报告 TASK-002 中发现的缺陷 —— findById 曾用 id 当作 List 下标
 * （return list.get(id)），当客户 id 与列表下标不一致时返回错误对象或抛
 * IndexOutOfBoundsException。
 *
 * 关键点：客户 id 故意取稀疏值（10/20/30），使其与列表下标（0/1/2）不同。
 */
class CustomerRepositoryRegressionTest {

    private CustomerRepository repository;

    @BeforeEach
    void setUp() {
        repository = new CustomerRepository();
    }

    private static Customer customer(int id, String name) {
        return new Customer(id, name, name + " Street", 30 + id);
    }

    /**
     * 缺陷复现：id=20 的客户位于列表下标 1。修复前 list.get(20) 会抛
     * IndexOutOfBoundsException。
     */
    @Test
    void findByIdReturnsMatchingCustomerWhenIdDiffersFromListIndex() {
        repository.save(customer(10, "Alice"));
        repository.save(customer(20, "Bob"));
        repository.save(customer(30, "Carol"));

        Customer found = repository.findById(20);

        assertEquals(20, found.getId(), "findById 必须返回 id 匹配的客户，而不是按下标取到的客户");
        assertEquals("Bob", found.getName());
    }

    @Test
    void findByIdReturnsSameInstanceForEveryStoredCustomer() {
        Customer alice = repository.save(customer(10, "Alice"));
        Customer bob = repository.save(customer(20, "Bob"));

        assertSame(alice, repository.findById(10));
        assertSame(bob, repository.findById(20));
    }

    /**
     * 目标客户位于列表末尾，遍历会先经过不匹配的条目。
     * 这覆盖了 findById 中「命中前跳过若干元素」的分支，
     * 而修复前正是这条路径按 id 取下标的错误所在。
     */
    @Test
    void findByIdFindsCustomerLocatedAtTheEndOfTheList() {
        repository.save(customer(10, "Alice"));
        repository.save(customer(20, "Bob"));
        repository.save(customer(30, "Carol"));

        Customer found = repository.findById(30);

        assertEquals(30, found.getId(), "列表末尾的客户必须能被正确定位");
        assertEquals("Carol", found.getName());
    }

    /** 修复前该用例会抛 IndexOutOfBoundsException，而不是返回 null。 */
    @Test
    void findByIdReturnsNullForUnknownIdWithoutThrowing() {
        repository.save(customer(10, "Alice"));

        assertNull(repository.findById(999));
    }

    @Test
    void findByIdReturnsNullOnEmptyRepository() {
        assertNull(repository.findById(0));
    }
}
